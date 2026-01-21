import os
import sys
import hashlib
import subprocess
import requests
import shutil
import shlex  # 新增：用于安全地拼接 shell 命令字符串
import uuid
import json
import fitz  # PyMuPDF
import asyncio
import logging
import traceback
import sqlite3
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from typing import Optional, Literal, Dict
from urllib.parse import quote, urlparse, unquote
from filelock import FileLock, Timeout
from fastapi import FastAPI, HTTPException, Query, Request, UploadFile, File, Form, Header, Depends
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
import uvicorn

# 添加项目根目录到路径
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(os.path.dirname(CURRENT_DIR))

# 工作目录配置
WORKSPACE_ROOT = os.path.abspath(os.path.join(CURRENT_DIR, '..', 'workspace'))
WORKSPACE_INPUT = os.path.join(WORKSPACE_ROOT, 'input')
WORKSPACE_OUTPUT = os.path.join(WORKSPACE_ROOT, 'output')

# 确保工作目录存在
os.makedirs(WORKSPACE_INPUT, exist_ok=True)
os.makedirs(WORKSPACE_OUTPUT, exist_ok=True)

# 文件锁目录
WORKSPACE_LOCKS = os.path.join(WORKSPACE_ROOT, 'locks')
os.makedirs(WORKSPACE_LOCKS, exist_ok=True)

# 数据库配置
DB_PATH = os.path.join(WORKSPACE_ROOT, 'ocr_service.db')

# mineru 配置
MINERU_VLM_URL = os.environ.get('MINERU_VLM_URL', 'http://10.104.255.37:30010')
MINERU_BACKEND = os.environ.get('MINERU_BACKEND', 'vlm-http-client')

# 服务配置
SERVER_HOST = os.environ.get('OCR_SERVER_HOST', '0.0.0.0')
SERVER_PORT = int(os.environ.get('OCR_SERVER_PORT', '8081'))
# DOWNLOAD_BASE_URL 将从请求中动态获取，这里保留环境变量作为 fallback
DOWNLOAD_BASE_URL = os.environ.get('OCR_DOWNLOAD_BASE_URL', None)

# 线程池配置
MAX_WORKERS = 30  # 最大并发线程数

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(os.path.join(WORKSPACE_ROOT, 'ocr_service.log'), encoding='utf-8')
    ]
)
logger = logging.getLogger(__name__)

# API Key 鉴权配置（支持多用户）
# 方式1: OCR_API_KEYS - JSON 格式字典 {"user1": "key1", "user2": "key2"}
# 方式2: OCR_API_KEY - 单个 key（向后兼容）
API_KEYS: Dict[str, str] = {}  # key -> user_id 的映射

api_keys_json = os.environ.get('OCR_API_KEYS', None)
if api_keys_json:
    try:
        # 支持 JSON 格式: {"user1": "key1", "user2": "key2"}
        user_keys = json.loads(api_keys_json)
        # 反转映射：从 user->key 转为 key->user
        API_KEYS = {key: user for user, key in user_keys.items()}
    except json.JSONDecodeError:
        print(f"警告: OCR_API_KEYS 格式错误，应为 JSON 格式")
elif os.environ.get('OCR_API_KEY', None):
    # 向后兼容单个 API key
    API_KEYS[os.environ.get('OCR_API_KEY')] = 'default'

# 线程池执行器（全局变量，在 lifespan 中初始化）
executor: Optional[ThreadPoolExecutor] = None


def init_database():
    """初始化数据库，创建表结构"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # 创建 API Keys 表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS api_keys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            api_key TEXT UNIQUE NOT NULL,
            user_id TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_active INTEGER DEFAULT 1,
            description TEXT
        )
    ''')
    
    # 创建使用日志表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS usage_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            md5 TEXT NOT NULL,
            api_key TEXT,
            user_id TEXT,
            ip_address TEXT,
            endpoint TEXT NOT NULL,
            filename TEXT,
            pdf_pages INTEGER,
            md_chars INTEGER,
            is_cached INTEGER DEFAULT 0,
            success INTEGER DEFAULT 1,
            error_message TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # 创建索引以提高查询性能
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_api_key ON api_keys(api_key)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_usage_md5 ON usage_logs(md5)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_usage_api_key ON usage_logs(api_key)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_usage_created_at ON usage_logs(created_at)')
    
    conn.commit()
    conn.close()
    print(f"[数据库] 数据库初始化完成: {DB_PATH}")


def migrate_api_keys_from_env():
    """从环境变量迁移 API Keys 到数据库（仅在数据库为空时执行）"""
    if not API_KEYS:
        return
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # 检查是否已有数据
    cursor.execute('SELECT COUNT(*) FROM api_keys')
    count = cursor.fetchone()[0]
    
    if count == 0:
        # 迁移环境变量中的 API Keys
        for api_key, user_id in API_KEYS.items():
            try:
                cursor.execute(
                    'INSERT INTO api_keys (api_key, user_id, description) VALUES (?, ?, ?)',
                    (api_key, user_id, '从环境变量迁移')
                )
                print(f"[数据库] 已迁移 API Key: {user_id}")
            except sqlite3.IntegrityError:
                # Key 已存在，跳过
                pass
        conn.commit()
    
    conn.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    global executor
    # 初始化数据库
    init_database()
    # 迁移环境变量中的 API Keys（如果有）
    migrate_api_keys_from_env()
    # 启动时创建线程池
    executor = ThreadPoolExecutor(max_workers=MAX_WORKERS)
    print(f"[启动] 线程池已创建，最大并发数: {MAX_WORKERS}")
    yield
    # 关闭时清理线程池
    if executor:
        executor.shutdown(wait=True)
        print("[关闭] 线程池已关闭")


# 创建 FastAPI 应用
app = FastAPI(
    title="MinerU OCR 服务",
    description="使用 MinerU 将 PDF 转换为 Markdown",
    version="1.0.0",
    lifespan=lifespan
)

# 挂载静态文件服务
app.mount("/files", StaticFiles(directory=WORKSPACE_ROOT), name="files")


class MinerUCommandParams(BaseModel):
    """
    MinerU 命令参数模型    
    https://opendatalab.github.io/MinerU/zh/usage/cli_tools/
    """
    path: str = Field(..., description="输入文件路径或目录（必填）")
    output: str = Field(..., description="输出目录（必填）")
    backend: Literal[
        "pipeline",
        "hybrid-auto-engine",
        "hybrid-http-client",
        "vlm-auto-engine",
        "vlm-http-client"
    ] = Field(default="vlm-http-client", description="解析后端")
    url: Optional[str] = Field(None, description="当使用 http-client 时，需指定服务地址")
    lang: Optional[Literal[
        "ch", "ch_server", "ch_lite", "en", "korean", "japan",
        "chinese_cht", "ta", "te", "ka", "th", "el", "latin",
        "arabic", "east_slavic", "cyrillic", "devanagari"
    ]] = Field(None, description="指定文档语言（可提升 OCR 准确率，仅用于 pipeline 与 hybrid* 后端）")
    formula: bool = Field(True, description="是否启用公式解析")
    table: bool = Field(True, description="是否启用表格解析")
    
    def to_command_list(self) -> list[str]:
        """将参数转换为命令行参数列表"""
        cmd = [
            'mineru',
            '-p', self.path,
            '-o', self.output,
            '-b', self.backend
        ]
        
        if self.url:
            cmd.extend(['-u', self.url])
        
        if self.lang:
            cmd.extend(['-l', self.lang])
        
        if not self.formula:
            cmd.extend(['-f', 'false'])
        
        if not self.table:
            cmd.extend(['-t', 'false'])
        
        return cmd


class MinerURequest(BaseModel):
    """MinerU 请求模型"""
    pdf_url: Optional[str] = None
    pdf_path: Optional[str] = None  # 新增：本地文件路径
    pdf_filename: Optional[str] = None
    vlm_url: Optional[str] = None
    backend: Optional[str] = None
    lang: Optional[str] = None  # 文档语言
    formula: Optional[bool] = None  # 是否启用公式解析
    table: Optional[bool] = None  # 是否启用表格解析


class MinerUResponse(BaseModel):
    """MinerU 响应模型"""
    model_config = {'extra': 'allow'}  # 允许额外字段
    
    success: bool
    message: str
    md5: Optional[str] = None
    input_path: Optional[str] = None
    output_path: Optional[str] = None
    files: Optional[list] = None
    download_urls: Optional[dict] = None


def calculate_file_md5(file_path: str) -> str:
    """计算文件的 MD5 值"""
    md5_hash = hashlib.md5()
    with open(file_path, 'rb') as f:
        for chunk in iter(lambda: f.read(8192), b''):
            md5_hash.update(chunk)
    return md5_hash.hexdigest()


def get_pdf_page_count(pdf_path: str) -> int:
    """
    使用 fitz (PyMuPDF) 计算 PDF 页数
    
    Args:
        pdf_path: PDF 文件路径
    
    Returns:
        PDF 文件的页数
    
    Raises:
        Exception: 如果无法打开或读取 PDF 文件
    """
    try:
        doc = fitz.open(pdf_path)
        page_count = doc.page_count
        doc.close()
        return page_count
    except Exception as e:
        # raise Exception(f"无法计算 PDF 页数: {str(e)}")
        print(f"无法计算 PDF 页数: {pdf_path} {str(e)}")
    return 0


def get_client_ip(request: Request) -> str:
    """
    获取客户端真实 IP 地址
    优先从代理头中获取，如果没有则使用直连 IP
    
    Args:
        request: FastAPI Request 对象
    
    Returns:
        客户端 IP 地址
    """
    # 优先从 X-Forwarded-For 获取（考虑代理情况）
    forwarded_for = request.headers.get('X-Forwarded-For')
    if forwarded_for:
        # X-Forwarded-For 可能包含多个 IP，取第一个
        return forwarded_for.split(',')[0].strip()
    
    # 其次从 X-Real-IP 获取
    real_ip = request.headers.get('X-Real-IP')
    if real_ip:
        return real_ip.strip()
    
    # 最后使用直连 IP
    if request.client:
        return request.client.host
    
    return 'unknown'


async def verify_api_key(authorization: Optional[str] = Header(None)) -> Optional[str]:
    """
    验证 Bearer Token 并返回用户标识
    从数据库中查询 API Key，如果数据库为空则不进行鉴权
    
    Args:
        authorization: 从请求头 Authorization 中获取的值（格式：Bearer <token>）
    
    Returns:
        用户标识字符串，如果未启用鉴权则返回 None
    
    Raises:
        HTTPException: Token 验证失败时抛出 401 或 403 错误
    """
    # 检查数据库中是否有 API Keys
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) FROM api_keys WHERE is_active = 1')
    count = cursor.fetchone()[0]
    conn.close()
    
    # 如果没有配置任何 API_KEYS，则不进行鉴权
    if count == 0:
        return None
    
    # 如果配置了 API_KEYS，则必须提供 Authorization header
    if not authorization:
        raise HTTPException(
            status_code=401,
            detail="缺少认证信息，请在请求头中提供 Authorization: Bearer <token>"
        )
    
    # 检查格式是否为 Bearer token
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != 'bearer':
        raise HTTPException(
            status_code=401,
            detail="Authorization 格式错误，应为: Bearer <token>"
        )
    
    token = parts[1]
    
    # 从数据库查询 token 是否有效
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        'SELECT user_id FROM api_keys WHERE api_key = ? AND is_active = 1',
        (token,)
    )
    result = cursor.fetchone()
    conn.close()
    
    if not result:
        raise HTTPException(
            status_code=403,
            detail="Token 无效"
        )
    
    return result[0]  # 返回 user_id


def get_lock_for_md5(md5: str) -> FileLock:
    """为指定的 MD5 获取文件锁"""
    lock_file = os.path.join(WORKSPACE_LOCKS, f"{md5}.lock")
    return FileLock(lock_file, timeout=300)  # 5分钟超时


def log_usage(
    md5: str,
    api_key: Optional[str],
    user_id: Optional[str],
    ip_address: str,
    endpoint: str,
    filename: str,
    pdf_pages: int,
    md_chars: int,
    is_cached: bool,
    success: bool = True,
    error_message: Optional[str] = None
):
    """
    记录 API 使用日志到数据库
    
    Args:
        md5: 文件 MD5
        api_key: API Key（如果有）
        user_id: 用户 ID（如果有）
        ip_address: 客户端 IP 地址
        endpoint: 调用的端点（/mineru 或 /file_mineru）
        filename: 原始文件名
        pdf_pages: PDF 页数
        md_chars: Markdown 文件字符数
        is_cached: 是否使用了缓存文件
        success: 是否成功
        error_message: 错误信息（如果失败）
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO usage_logs 
            (md5, api_key, user_id, ip_address, endpoint, filename, 
             pdf_pages, md_chars, is_cached, success, error_message)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            md5, api_key, user_id, ip_address, endpoint, filename,
            pdf_pages, md_chars, 1 if is_cached else 0, 1 if success else 0, error_message
        ))
        
        conn.commit()
        conn.close()
        logger.info(f"[日志] 已记录使用日志 - MD5: {md5}, 用户: {user_id}, 端点: {endpoint}, 缓存: {is_cached}")
    except Exception as e:
        logger.error(f"[日志] 记录使用日志失败: {str(e)}", exc_info=True)


def save_original_filename(input_dir: str, original_filename: str):
    """保存原始文件名到 filename.txt"""
    filename_file = os.path.join(input_dir, 'filename.txt')
    # 如果文件已存在，追加新文件名（避免覆盖）
    mode = 'a' if os.path.exists(filename_file) else 'w'
    with open(filename_file, mode, encoding='utf-8') as f:
        # 检查是否已记录过该文件名
        if mode == 'a':
            with open(filename_file, 'r', encoding='utf-8') as rf:
                existing_names = rf.read().strip().split('\n')
                if original_filename in existing_names:
                    return
        f.write(original_filename + '\n')


def get_original_filename(input_dir: str) -> Optional[str]:
    """读取原始文件名（返回第一个记录的文件名）"""
    filename_file = os.path.join(input_dir, 'filename.txt')
    if os.path.exists(filename_file):
        try:
            with open(filename_file, 'r', encoding='utf-8') as f:
                first_line = f.readline().strip()
                return first_line if first_line else None
        except Exception as e:
            print(f"读取 filename.txt 失败: {str(e)}")
    return None


def get_filename_from_url(pdf_url: str) -> str:
    """从 URL 中提取文件名"""
    parsed = urlparse(pdf_url)
    path = unquote(parsed.path)
    filename = os.path.basename(path)
    if not filename or not filename.lower().endswith('.pdf'):
        filename = 'document.pdf'
    return filename


def download_pdf(pdf_url: str, save_path: str) -> bool:
    """下载 PDF 文件到指定路径"""
    try:
        logger.info(f"开始下载 PDF: {pdf_url}")
        response = requests.get(pdf_url, stream=True, timeout=300)
        response.raise_for_status()
        
        with open(save_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        logger.info(f"PDF 下载成功: {pdf_url} -> {save_path}")
        return True
    except requests.exceptions.RequestException as e:
        logger.error(f"下载 PDF 失败 - URL: {pdf_url}, 错误: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"下载 PDF 失败: {str(e)}")
    except Exception as e:
        logger.error(f"保存 PDF 文件失败 - 路径: {save_path}, 错误: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"保存 PDF 失败: {str(e)}")


def run_mineru(params: MinerUCommandParams) -> tuple[bool, str]:
    """
    运行 mineru 命令处理 PDF
    
    Args:
        params: MinerU 命令参数对象
    
    Returns:
        (success, message): 执行结果
    """
    # 使用 MinerUCommandParams 的 to_command_list 方法生成命令
    cmd = params.to_command_list()
    
    # 输出实际执行的 shell 脚本到日志
    # 使用 shlex.join 可以正确处理路径中的空格和特殊字符
    cmd_script = shlex.join(cmd)
    print(f"[MinerU Exec] {cmd_script}")
    
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=1800  # 30分钟超时
        )
        
        if result.returncode == 0:
            logger.info(f"MinerU 执行成功: {params.path}")
            return True, result.stdout
        else:
            error_msg = f"mineru 执行失败: {result.stderr}"
            logger.error(f"MinerU 执行失败 - 命令: {cmd_script}, 返回码: {result.returncode}, 错误: {result.stderr}")
            return False, error_msg
    except subprocess.TimeoutExpired:
        error_msg = "mineru 执行超时（30分钟）"
        logger.error(f"MinerU 执行超时 - 命令: {cmd_script}, 文件: {params.path}")
        return False, error_msg
    except FileNotFoundError:
        error_msg = "mineru 命令未找到，请确保已安装 MinerU"
        logger.error(f"MinerU 命令未找到 - 命令: {cmd_script}")
        return False, error_msg
    except Exception as e:
        error_msg = f"执行 mineru 时发生错误: {str(e)}"
        logger.error(f"MinerU 执行异常 - 命令: {cmd_script}, 错误: {str(e)}", exc_info=True)
        return False, error_msg


def get_output_files(output_dir: str) -> list:
    """获取输出目录下的所有文件"""
    files = []
    if os.path.exists(output_dir):
        for root, _, filenames in os.walk(output_dir):
            for filename in filenames:
                file_path = os.path.join(root, filename)
                rel_path = os.path.relpath(file_path, output_dir)
                files.append({
                    'name': filename,
                    'path': rel_path,
                    'size': os.path.getsize(file_path)
                })
    return files


def generate_download_urls(md5: str, files: list, base_url: str) -> dict:
    """生成文件下载链接"""
    urls = {}
    for file_info in files:
        rel_path = f"output/{md5}/{file_info['path']}"
        encoded_path = quote(rel_path, safe='/')
        urls[file_info['name']] = f"{base_url}/files/{encoded_path}"
    return urls


def read_md_content(output_dir: str, md5: str) -> Optional[str]:
    """
    读取 markdown 文件内容并返回。
    统一查找 {md5}.md 文件，如果找不到则返回第一个找到的 .md 文件。
    """
    if not os.path.exists(output_dir):
        return None
    
    # 优先查找以 md5 命名的 md 文件
    target_md = f"{md5}.md"
    first_md_path = None

    for root, _, filenames in os.walk(output_dir):
        for filename in filenames:
            if filename.endswith('.md'):
                file_path = os.path.join(root, filename)
                
                # 记录第一个找到的 md 文件路径，作为兜底
                if first_md_path is None:
                    first_md_path = file_path

                # 优先匹配 md5 命名的文件
                if filename == target_md:
                    try:
                        with open(file_path, 'r', encoding='utf-8') as f:
                            return f.read()
                    except Exception as e:
                        print(f"读取文件 {file_path} 失败: {str(e)}")
                        pass

    # 如果没找到 md5.md，返回第一个找到的 md 文件
    if first_md_path:
        try:
            with open(first_md_path, 'r', encoding='utf-8') as f:
                return f.read()
        except Exception as e:
            print(f"读取文件 {first_md_path} 失败: {str(e)}")
    
    return None


def _process_pdf_task(
    temp_pdf_path: str, 
    filename: str, 
    base_url: str,
    mineru_params: MinerUCommandParams
) -> tuple[MinerUResponse, int, int, bool]:
    """
    核心处理逻辑（带并发保护）：
    1. 计算 MD5
    2. 获取文件锁（防止并发处理同一文件）
    3. 移动文件到正式目录（统一使用 md5.pdf 命名）
    4. 检查是否已处理
    5. 运行 mineru
    6. 返回结果
    
    Args:
        temp_pdf_path: 临时 PDF 文件路径
        filename: 原始文件名（用于记录）
        base_url: 下载基础 URL
        mineru_params: MinerU 命令参数（注意：path 和 output 会被重新设置）
    
    Returns:
        (response, pdf_pages, md_chars, is_cached): 响应对象、PDF页数、MD字符数、是否使用缓存
    """
    # 计算 MD5
    md5 = calculate_file_md5(temp_pdf_path)
    
    # 获取文件锁，防止并发处理同一个 MD5
    lock = get_lock_for_md5(md5)
    
    try:
        with lock:
            pdf_pages = get_pdf_page_count(temp_pdf_path)

            # 创建基于 MD5 的输入目录
            input_dir = os.path.join(WORKSPACE_INPUT, md5)
            os.makedirs(input_dir, exist_ok=True)
            
            # 统一使用 md5.pdf 作为文件名
            input_pdf_path = os.path.join(input_dir, f"{md5}.pdf")
            
            # 保存原始文件名
            save_original_filename(input_dir, filename)
            
            # 移动文件到正式目录
            if os.path.exists(input_pdf_path):
                os.remove(temp_pdf_path)  # 文件已存在，删除临时文件
            else:
                os.rename(temp_pdf_path, input_pdf_path)
            
            # 输出目录
            output_dir = os.path.join(WORKSPACE_OUTPUT, md5)
            
            # 检查是否已处理过（输出目录存在且有 .md 文件）
            is_cached = False
            if os.path.exists(output_dir) and any(
                f.endswith('.md') 
                for root, dirs, files in os.walk(output_dir) 
                for f in files
            ):
                is_cached = True

            if is_cached:
                files = get_output_files(output_dir)
                download_urls = generate_download_urls(md5, files, base_url)
                md_content = read_md_content(output_dir, md5)
                md_chars = len(md_content) if md_content else 0
                
                # 获取原始文件名用于日志
                original_filename = get_original_filename(input_dir) or filename
                
                response = MinerUResponse(
                    success=True,
                    message=f"文件已处理过，直接返回结果（原始文件名: {original_filename}）",
                    md5=md5,
                    input_path=input_pdf_path,
                    output_path=output_dir,
                    files=files,
                    download_urls=download_urls
                )
                # 统一使用 md5.md 作为动态字段名
                if md_content:
                    setattr(response, f"{md5}.md", md_content)
                return response, pdf_pages, md_chars, True
            
            # 创建输出目录
            os.makedirs(output_dir, exist_ok=True)
            
            # 更新 mineru 参数中的路径
            mineru_params.path = input_pdf_path
            mineru_params.output = output_dir
            
            # 运行 mineru
            print(f"[MinerU] 开始处理文件: {filename} (MD5: {md5})")
            success, message = run_mineru(mineru_params)
            
            if not success:
                response = MinerUResponse(
                    success=False,
                    message=message,
                    md5=md5,
                    input_path=input_pdf_path,
                    output_path=output_dir
                )
                return response, pdf_pages, 0, False
            
            # 获取输出文件列表
            files = get_output_files(output_dir)
            download_urls = generate_download_urls(md5, files, base_url)
            md_content = read_md_content(output_dir, md5)
            md_chars = len(md_content) if md_content else 0
            
            response = MinerUResponse(
                success=True,
                message=f"处理成功（原始文件名: {filename}）",
                md5=md5,
                input_path=input_pdf_path,
                output_path=output_dir,
                files=files,
                download_urls=download_urls
            )
            # 统一使用 md5.md 作为动态字段名
            if md_content:
                setattr(response, f"{md5}.md", md_content)
            return response, pdf_pages, md_chars, False
            
    except Timeout:
        # 清理临时文件
        if os.path.exists(temp_pdf_path):
            os.remove(temp_pdf_path)
        error_msg = f"文件正在被其他请求处理，请稍后重试（MD5: {md5}）"
        logger.warning(f"文件锁超时 - MD5: {md5}, 文件名: {filename}")
        raise HTTPException(status_code=409, detail=error_msg)
    except HTTPException:
        # 重新抛出 HTTP 异常
        raise
    except Exception as e:
        # 清理临时文件
        if os.path.exists(temp_pdf_path):
            os.remove(temp_pdf_path)
        error_msg = f"处理 PDF 任务时发生错误: {str(e)}"
        logger.error(f"处理 PDF 任务异常 - MD5: {md5}, 文件名: {filename}, 错误: {str(e)}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=error_msg)


@app.get("/")
async def root():
    """根路径，返回服务信息"""
    return {
        "service": "MinerU OCR 服务",
        "version": "1.0.0",
        "workspace": WORKSPACE_ROOT,
        "mineru_vlm_url": MINERU_VLM_URL,
        "mineru_backend": MINERU_BACKEND
    }


@app.post("/mineru", response_model=MinerUResponse)
async def mineru_endpoint(
    body: MinerURequest, 
    request: Request,
    api_key: Optional[str] = Depends(verify_api_key)
):
    """
    MinerU PDF 转 Markdown 接口
    
    支持两种方式：
    1. pdf_url: 从 URL 下载 PDF
    2. pdf_path: 使用本地文件路径
    
    处理流程：
    1. 获取或下载 PDF 到本地 workspace/input/<md5>/
    2. 调用 mineru 处理 PDF
    3. 返回输出文件的下载链接
    """
    # 获取客户端 IP
    client_ip = get_client_ip(request)
    user_info = f"User: {api_key}" if api_key else "No Auth"
    logger.info(f"[API Request] /mineru - {user_info}, IP: {client_ip}, Body: pdf_url={body.pdf_url}, pdf_path={body.pdf_path}")
    
    # 获取请求的 base_url
    base_url = DOWNLOAD_BASE_URL or str(request.base_url).rstrip('/')
    
    # 验证参数
    if not body.pdf_url and not body.pdf_path:
        logger.warning(f"[API Request] /mineru - 参数错误: 缺少 pdf_url 或 pdf_path, IP: {client_ip}")
        raise HTTPException(status_code=400, detail="必须提供 pdf_url 或 pdf_path")
    
    if body.pdf_url and body.pdf_path:
        logger.warning(f"[API Request] /mineru - 参数错误: 同时提供了 pdf_url 和 pdf_path, IP: {client_ip}")
        raise HTTPException(status_code=400, detail="只能提供 pdf_url 或 pdf_path 其中一个")
    
    try:
        # 创建临时目录
        temp_dir = os.path.join(WORKSPACE_INPUT, 'temp')
        os.makedirs(temp_dir, exist_ok=True)
        
        # 生成唯一的临时文件名，避免并发冲突
        temp_filename = f"{uuid.uuid4().hex}.pdf"
        temp_pdf_path = os.path.join(temp_dir, temp_filename)
        
        # 处理本地文件路径
        if body.pdf_path:
            if not os.path.exists(body.pdf_path):
                logger.error(f"[API Request] /mineru - 文件不存在: {body.pdf_path}, IP: {client_ip}")
                raise HTTPException(status_code=404, detail=f"文件不存在: {body.pdf_path}")
            
            if not body.pdf_path.lower().endswith('.pdf'):
                logger.warning(f"[API Request] /mineru - 文件类型错误: {body.pdf_path}, IP: {client_ip}")
                raise HTTPException(status_code=400, detail="只支持 PDF 文件")
            
            filename = body.pdf_filename or os.path.basename(body.pdf_path)
            
            # 复制文件到临时目录
            try:
                shutil.copy2(body.pdf_path, temp_pdf_path)
                logger.info(f"本地文件复制成功: {body.pdf_path} -> {temp_pdf_path}")
            except Exception as e:
                logger.error(f"复制本地文件失败 - 源: {body.pdf_path}, 目标: {temp_pdf_path}, 错误: {str(e)}", exc_info=True)
                raise HTTPException(status_code=500, detail=f"复制文件失败: {str(e)}")
        else:
            # 处理 URL 下载
            filename = body.pdf_filename or get_filename_from_url(body.pdf_url)
            
            # 下载 PDF
            download_pdf(body.pdf_url, temp_pdf_path)
        
        # 创建 MinerU 命令参数
        mineru_params = MinerUCommandParams(
            path="",  # 将在 _process_pdf_task 中设置
            output="",  # 将在 _process_pdf_task 中设置
            backend=body.backend or MINERU_BACKEND,
            url=body.vlm_url or MINERU_VLM_URL,
            lang=body.lang,
            formula=body.formula if body.formula is not None else True,
            table=body.table if body.table is not None else True
        )
        
        # 使用线程池异步执行处理任务
        logger.info(f"提交处理任务到线程池 - 文件: {filename}, IP: {client_ip}")
        loop = asyncio.get_event_loop()
        result_tuple = await loop.run_in_executor(
            executor,
            _process_pdf_task,
            temp_pdf_path,
            filename,
            base_url,
            mineru_params
        )
        
        # 解包结果
        result, pdf_pages, md_chars, is_cached = result_tuple
        
        # 记录使用日志
        # 从 Authorization header 中提取原始 token（如果有）
        authorization = request.headers.get('Authorization')
        original_token = None
        if authorization:
            parts = authorization.split()
            if len(parts) == 2 and parts[0].lower() == 'bearer':
                original_token = parts[1]
        
        log_usage(
            md5=result.md5,
            api_key=original_token,
            user_id=api_key,
            ip_address=client_ip,
            endpoint="/mineru",
            filename=filename,
            pdf_pages=pdf_pages,
            md_chars=md_chars,
            is_cached=is_cached,
            success=result.success,
            error_message=None if result.success else result.message
        )
        
        logger.info(f"处理任务完成 - 文件: {filename}, MD5: {result.md5}, 成功: {result.success}, IP: {client_ip}")
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[API Request] /mineru - 未预期错误, IP: {client_ip}, 错误: {str(e)}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"处理请求时发生错误: {str(e)}")


@app.post("/file_mineru", response_model=MinerUResponse)
async def file_mineru_endpoint(
    request: Request,
    file: UploadFile = File(..., description="PDF 文件"),
    pdf_filename: Optional[str] = Form(None, description="指定保存时的文件名"),
    vlm_url: Optional[str] = Form(None, description="VLM 服务地址"),
    backend: Optional[str] = Form(None, description="MinerU 后端类型"),
    lang: Optional[str] = Form(None, description="文档语言"),
    formula: Optional[bool] = Form(None, description="是否启用公式解析"),
    table: Optional[bool] = Form(None, description="是否启用表格解析"),
    api_key: Optional[str] = Depends(verify_api_key)
):
    """
    通过文件上传方式处理 PDF
    
    处理流程：
    1. 接收上传的文件保存到本地临时目录
    2. 调用 mineru 处理 PDF
    3. 返回输出文件的下载链接
    """
    # 获取客户端 IP
    client_ip = get_client_ip(request)
    user_info = f"User: {api_key}" if api_key else "No Auth"
    logger.info(f"[API Request] /file_mineru - {user_info}, IP: {client_ip}, File: {file.filename}")
    
    base_url = DOWNLOAD_BASE_URL or str(request.base_url).rstrip('/')
    
    # 验证文件类型
    if not file.filename:
        logger.warning(f"[API Request] /file_mineru - 未提供文件, IP: {client_ip}")
        raise HTTPException(status_code=400, detail="未提供文件")
    
    if not file.filename.lower().endswith('.pdf'):
        logger.warning(f"[API Request] /file_mineru - 文件类型错误: {file.filename}, IP: {client_ip}")
        raise HTTPException(status_code=400, detail="只支持 PDF 文件")

    filename = pdf_filename or file.filename

    try:
        # 创建临时目录
        temp_dir = os.path.join(WORKSPACE_INPUT, 'temp')
        os.makedirs(temp_dir, exist_ok=True)
        
        # 使用 UUID 生成唯一的临时文件名，避免并发冲突
        temp_filename = f"{uuid.uuid4().hex}.pdf"
        temp_pdf_path = os.path.join(temp_dir, temp_filename)
        
        try:
            # 保存上传的文件
            logger.info(f"开始保存上传文件: {filename} -> {temp_pdf_path}")
            with open(temp_pdf_path, "wb") as buffer:
                shutil.copyfileobj(file.file, buffer)
            logger.info(f"文件保存成功: {temp_pdf_path}")
        except Exception as e:
            logger.error(f"保存上传文件失败 - 文件: {filename}, 路径: {temp_pdf_path}, 错误: {str(e)}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"保存文件失败: {str(e)}")
        finally:
            file.file.close()
    
        # 创建 MinerU 命令参数
        mineru_params = MinerUCommandParams(
            path="",  # 将在 _process_pdf_task 中设置
            output="",  # 将在 _process_pdf_task 中设置
            backend=backend or MINERU_BACKEND,
            url=vlm_url or MINERU_VLM_URL,
            lang=lang,
            formula=formula if formula is not None else True,
            table=table if table is not None else True
        )
        
        # 使用线程池异步执行处理任务
        logger.info(f"提交处理任务到线程池 - 文件: {filename}, IP: {client_ip}")
        loop = asyncio.get_event_loop()
        result_tuple = await loop.run_in_executor(
            executor,
            _process_pdf_task,
            temp_pdf_path,
            filename,
            base_url,
            mineru_params
        )
        
        # 解包结果
        result, pdf_pages, md_chars, is_cached = result_tuple
        
        # 记录使用日志
        # 从 Authorization header 中提取原始 token（如果有）
        authorization = request.headers.get('Authorization')
        original_token = None
        if authorization:
            parts = authorization.split()
            if len(parts) == 2 and parts[0].lower() == 'bearer':
                original_token = parts[1]
        
        log_usage(
            md5=result.md5,
            api_key=original_token,
            user_id=api_key,
            ip_address=client_ip,
            endpoint="/file_mineru",
            filename=filename,
            pdf_pages=pdf_pages,
            md_chars=md_chars,
            is_cached=is_cached,
            success=result.success,
            error_message=None if result.success else result.message
        )
        
        logger.info(f"处理任务完成 - 文件: {filename}, MD5: {result.md5}, 成功: {result.success}, IP: {client_ip}")
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[API Request] /file_mineru - 未预期错误, IP: {client_ip}, 文件: {file.filename}, 错误: {str(e)}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"处理请求时发生错误: {str(e)}")


@app.get("/status/{md5}")
async def get_status(md5: str, request: Request):
    """查询处理状态和结果"""
    # 获取请求的 base_url
    base_url = DOWNLOAD_BASE_URL or str(request.base_url).rstrip('/')
    
    input_dir = os.path.join(WORKSPACE_INPUT, md5)
    output_dir = os.path.join(WORKSPACE_OUTPUT, md5)
    
    if not os.path.exists(input_dir):
        raise HTTPException(status_code=404, detail="未找到对应的任务")
    
    # 获取输入文件
    input_files = os.listdir(input_dir) if os.path.exists(input_dir) else []
    
    # 获取输出文件
    output_files = get_output_files(output_dir) if os.path.exists(output_dir) else []
    download_urls = generate_download_urls(md5, output_files, base_url) if output_files else {}
    
    return {
        "md5": md5,
        "input_files": input_files,
        "output_files": output_files,
        "download_urls": download_urls,
        "status": "completed" if output_files else "pending"
    }


@app.get("/list")
async def list_tasks(
    type: str = Query("all", description="类型: input, output, all")
):
    """列出所有任务"""
    result = {}
    
    if type in ["input", "all"]:
        input_tasks = []
        if os.path.exists(WORKSPACE_INPUT):
            for md5 in os.listdir(WORKSPACE_INPUT):
                if md5 != 'temp' and os.path.isdir(os.path.join(WORKSPACE_INPUT, md5)):
                    input_tasks.append(md5)
        result["input_tasks"] = input_tasks
    
    if type in ["output", "all"]:
        output_tasks = []
        if os.path.exists(WORKSPACE_OUTPUT):
            for md5 in os.listdir(WORKSPACE_OUTPUT):
                if os.path.isdir(os.path.join(WORKSPACE_OUTPUT, md5)):
                    output_tasks.append(md5)
        result["output_tasks"] = output_tasks
    
    return result


if __name__ == "__main__":
    # reload=True 启用热重载，代码修改后自动重启
    uvicorn.run("services.apis_ocr:app", host=SERVER_HOST, port=SERVER_PORT, reload=True)

# 使用 curl 测试新接口 /file_mineru
# curl -X POST "http://localhost:8081/file_mineru" \
#   -F "file=@/path/to/local/document.pdf" \
#   -F "pdf_filename=custom_name.pdf"

