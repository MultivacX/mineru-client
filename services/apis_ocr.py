import os
import sys
import hashlib
import subprocess
import requests
import shutil
import shlex  # 新增：用于安全地拼接 shell 命令字符串
from typing import Optional, Literal
from urllib.parse import quote, urlparse, unquote
from fastapi import FastAPI, HTTPException, Query, Request, UploadFile, File, Form
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

# mineru 配置
MINERU_VLM_URL = os.environ.get('MINERU_VLM_URL', 'http://10.104.255.37:30010')
MINERU_BACKEND = os.environ.get('MINERU_BACKEND', 'vlm-http-client')

# 服务配置
SERVER_HOST = os.environ.get('OCR_SERVER_HOST', '0.0.0.0')
SERVER_PORT = int(os.environ.get('OCR_SERVER_PORT', '8081'))
# DOWNLOAD_BASE_URL 将从请求中动态获取，这里保留环境变量作为 fallback
DOWNLOAD_BASE_URL = os.environ.get('OCR_DOWNLOAD_BASE_URL', None)

# 创建 FastAPI 应用
app = FastAPI(
    title="MinerU OCR 服务",
    description="使用 MinerU 将 PDF 转换为 Markdown",
    version="1.0.0"
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
        response = requests.get(pdf_url, stream=True, timeout=300)
        response.raise_for_status()
        
        with open(save_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        return True
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"下载 PDF 失败: {str(e)}")


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
            return True, result.stdout
        else:
            return False, f"mineru 执行失败: {result.stderr}"
    except subprocess.TimeoutExpired:
        return False, "mineru 执行超时"
    except FileNotFoundError:
        return False, "mineru 命令未找到，请确保已安装 MinerU"
    except Exception as e:
        return False, f"执行 mineru 时发生错误: {str(e)}"


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


def read_md_content(output_dir: str, input_filename: str) -> Optional[str]:
    """
    读取 markdown 文件内容并返回。
    优先级：
    1. 查找与输入文件同名的 md 文件（不含扩展名）
    2. 如果没找到同名文件，返回找到的第一个 .md 文件（针对不同文件名处理同一内容的情况）
    """
    if not os.path.exists(output_dir):
        return None
    
    # 尝试查找与输入文件同名的 md 文件（不含扩展名）
    base_name = os.path.splitext(input_filename)[0]
    target_md = f"{base_name}.md"
    
    first_md_path = None # 用于存放找到的第一个 md 文件路径，用于兜底

    for root, _, filenames in os.walk(output_dir):
        for filename in filenames:
            if filename.endswith('.md'):
                file_path = os.path.join(root, filename)
                
                # 记录第一个找到的 md 文件路径，作为兜底
                if first_md_path is None:
                    first_md_path = file_path

                # 优先匹配同名文件
                if filename == target_md:
                    try:
                        with open(file_path, 'r', encoding='utf-8') as f:
                            return f.read()
                    except Exception as e:
                        print(f"读取文件 {file_path} 失败: {str(e)}")
                        # 即使读取失败，也可以尝试使用第一个 md 文件
                        pass

    # 如果没找到同名的，但找到了其他的 md 文件，则返回第一个找到的
    # 这是为了修复：已处理过的文件，如果传入其他的 filename，导致没有 .md 内容的问题
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
) -> MinerUResponse:
    """
    核心处理逻辑：
    1. 计算 MD5
    2. 移动文件到正式目录
    3. 检查是否已处理
    4. 运行 mineru
    5. 返回结果
    
    Args:
        temp_pdf_path: 临时 PDF 文件路径
        filename: 文件名
        base_url: 下载基础 URL
        mineru_params: MinerU 命令参数（注意：path 和 output 会被重新设置）
    """
    # 计算 MD5
    md5 = calculate_file_md5(temp_pdf_path)
    
    # 创建基于 MD5 的输入目录
    input_dir = os.path.join(WORKSPACE_INPUT, md5)
    os.makedirs(input_dir, exist_ok=True)
    
    # 移动文件到正式目录
    input_pdf_path = os.path.join(input_dir, filename)
    if os.path.exists(input_pdf_path):
        os.remove(temp_pdf_path)  # 文件已存在，删除临时文件
    else:
        os.rename(temp_pdf_path, input_pdf_path)
    
    # 输出目录
    output_dir = os.path.join(WORKSPACE_OUTPUT, md5)
    
    # 检查是否已处理过（输出目录存在且有文件）
    # 判断是否存在 .md 文件，避免部分处理失败的情况
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
        md_content = read_md_content(output_dir, filename)
        
        response = MinerUResponse(
            success=True,
            message="文件已处理过，直接返回结果",
            md5=md5,
            input_path=input_pdf_path,
            output_path=output_dir,
            files=files,
            download_urls=download_urls
        )
        if md_content:
            setattr(response, md5 + ".md" if not filename else os.path.splitext(filename)[0] + ".md", md_content)
        return response
    
    # 创建输出目录
    os.makedirs(output_dir, exist_ok=True)
    
    # 更新 mineru 参数中的路径
    mineru_params.path = input_pdf_path
    mineru_params.output = output_dir
    
    # 运行 mineru
    success, message = run_mineru(mineru_params)
    
    if not success:
        return MinerUResponse(
            success=False,
            message=message,
            md5=md5,
            input_path=input_pdf_path,
            output_path=output_dir
        )
    
    # 获取输出文件列表
    files = get_output_files(output_dir)
    download_urls = generate_download_urls(md5, files, base_url)
    md_content = read_md_content(output_dir, filename)
    
    response = MinerUResponse(
        success=True,
        message="处理成功",
        md5=md5,
        input_path=input_pdf_path,
        output_path=output_dir,
        files=files,
        download_urls=download_urls
    )
    # 将 md 内容作为动态字段添加
    if md_content:
        setattr(response, md5 + ".md" if not filename else os.path.splitext(filename)[0] + ".md", md_content)
    return response


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
async def mineru_endpoint(body: MinerURequest, request: Request):
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
    # 获取请求的 base_url
    base_url = DOWNLOAD_BASE_URL or str(request.base_url).rstrip('/')
    
    # 验证参数
    if not body.pdf_url and not body.pdf_path:
        raise HTTPException(status_code=400, detail="必须提供 pdf_url 或 pdf_path")
    
    if body.pdf_url and body.pdf_path:
        raise HTTPException(status_code=400, detail="只能提供 pdf_url 或 pdf_path 其中一个")
    
    # 创建临时目录
    temp_dir = os.path.join(WORKSPACE_INPUT, 'temp')
    os.makedirs(temp_dir, exist_ok=True)
    
    # 处理本地文件路径
    if body.pdf_path:
        if not os.path.exists(body.pdf_path):
            raise HTTPException(status_code=404, detail=f"文件不存在: {body.pdf_path}")
        
        if not body.pdf_path.lower().endswith('.pdf'):
            raise HTTPException(status_code=400, detail="只支持 PDF 文件")
        
        filename = body.pdf_filename or os.path.basename(body.pdf_path)
        temp_pdf_path = os.path.join(temp_dir, filename)
        
        # 复制文件到临时目录
        shutil.copy2(body.pdf_path, temp_pdf_path)
    else:
        # 处理 URL 下载
        filename = body.pdf_filename or get_filename_from_url(body.pdf_url)
        temp_pdf_path = os.path.join(temp_dir, filename)
        
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
    
    return _process_pdf_task(temp_pdf_path, filename, base_url, mineru_params)


@app.post("/file_mineru", response_model=MinerUResponse)
async def file_mineru_endpoint(
    request: Request,
    file: UploadFile = File(..., description="PDF 文件"),
    pdf_filename: Optional[str] = Form(None, description="指定保存时的文件名"),
    vlm_url: Optional[str] = Form(None, description="VLM 服务地址"),
    backend: Optional[str] = Form(None, description="MinerU 后端类型"),
    lang: Optional[str] = Form(None, description="文档语言"),
    formula: Optional[bool] = Form(None, description="是否启用公式解析"),
    table: Optional[bool] = Form(None, description="是否启用表格解析")
):
    """
    通过文件上传方式处理 PDF
    
    处理流程：
    1. 接收上传的文件保存到本地临时目录
    2. 调用 mineru 处理 PDF
    3. 返回输出文件的下载链接
    """
    base_url = DOWNLOAD_BASE_URL or str(request.base_url).rstrip('/')
    
    # 验证文件类型
    if not file.filename:
        raise HTTPException(status_code=400, detail="未提供文件")
    
    if not file.filename.lower().endswith('.pdf'):
        raise HTTPException(status_code=400, detail="只支持 PDF 文件")

    filename = pdf_filename or file.filename

    # 创建临时目录
    temp_dir = os.path.join(WORKSPACE_INPUT, 'temp')
    os.makedirs(temp_dir, exist_ok=True)
    
    temp_pdf_path = os.path.join(temp_dir, filename)
    
    try:
        # 保存上传的文件
        with open(temp_pdf_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
    except Exception as e:
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
    
    return _process_pdf_task(temp_pdf_path, filename, base_url, mineru_params)


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

