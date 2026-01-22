import os
import sys
import requests
import shutil
import uuid
import json
import asyncio
import logging
import traceback
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from typing import Optional, Dict
from urllib.parse import urlparse, unquote
from fastapi import FastAPI, HTTPException, Request, UploadFile, File, Form, Header, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
import uvicorn

# 添加项目根目录到路径
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(os.path.dirname(CURRENT_DIR))

# 工作目录配置（仅用于临时文件存储）
WORKSPACE_ROOT = os.path.abspath(os.path.join(CURRENT_DIR, '..', 'workspace'))
WORKSPACE_TEMP = os.path.join(WORKSPACE_ROOT, 'temp')

# 确保临时目录存在
os.makedirs(WORKSPACE_TEMP, exist_ok=True)

# mineru 后端服务 URL
MINERU_VLM_URL = os.environ.get('MINERU_VLM_URL', 'http://10.104.255.37:30010')

# 服务配置
SERVER_HOST = os.environ.get('OCR_SERVER_HOST', '0.0.0.0')
SERVER_PORT = int(os.environ.get('OCR_SERVER_PORT', '8081'))

# 线程池配置
MAX_WORKERS = 30  # 最大并发线程数

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(os.path.join(WORKSPACE_ROOT, 'forward_service.log'), encoding='utf-8')
    ]
)
logger = logging.getLogger(__name__)

# 线程池执行器（全局变量，在 lifespan 中初始化）
executor: Optional[ThreadPoolExecutor] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    global executor
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
    title="MinerU 转发服务",
    description="转发 PDF 处理请求到 MinerU 后端服务",
    version="1.0.0",
    lifespan=lifespan
)


class MinerURequest(BaseModel):
    """MinerU 请求模型"""
    pdf_url: Optional[str] = None
    pdf_path: Optional[str] = None
    pdf_filename: Optional[str] = None
    vlm_url: Optional[str] = None
    backend: Optional[str] = None
    lang: Optional[str] = None
    formula: Optional[bool] = None
    table: Optional[bool] = None


class MinerUResponse(BaseModel):
    """MinerU 响应模型（直接透传后端响应）"""
    model_config = {'extra': 'allow'}


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
        logger.info(f"PDF 下载成功: {save_path}")
        return True
    except Exception as e:
        logger.error(f"下载 PDF 失败 - URL: {pdf_url}, 错误: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"下载 PDF 失败: {str(e)}")


def forward_to_backend(
    file_path: str,
    filename: str,
    client_ip: str,
    authorization: Optional[str] = None,
    vlm_url: Optional[str] = None,
    backend: Optional[str] = None,
    lang: Optional[str] = None,
    formula: Optional[bool] = None,
    table: Optional[bool] = None
) -> dict:
    """
    将文件转发到后端 MinerU 服务
    
    Args:
        file_path: 本地文件路径
        filename: 文件名
        client_ip: 客户端 IP 地址
        authorization: Authorization header（透传）
        vlm_url: VLM 服务地址
        backend: MinerU 后端类型
        lang: 文档语言
        formula: 是否启用公式解析
        table: 是否启用表格解析
    
    Returns:
        后端响应的 JSON 数据
    """
    backend_url = f"{MINERU_VLM_URL.rstrip('/')}/file_mineru"
    
    try:
        logger.info(f"转发请求到后端: {backend_url}, 文件: {filename}, IP: {client_ip}")
        
        # 准备请求头（透传 Authorization 和 IP）
        headers = {
            'X-Forwarded-For': client_ip,
            'X-Real-IP': client_ip
        }
        if authorization:
            headers['Authorization'] = authorization
        
        # 准备 multipart/form-data
        with open(file_path, 'rb') as f:
            files = {'file': (filename, f, 'application/pdf')}
            data = {}
            
            if filename:
                data['pdf_filename'] = filename
            if vlm_url:
                data['vlm_url'] = vlm_url
            if backend:
                data['backend'] = backend
            if lang:
                data['lang'] = lang
            if formula is not None:
                data['formula'] = str(formula).lower()
            if table is not None:
                data['table'] = str(table).lower()
            
            # 发送请求到后端（携带 headers）
            response = requests.post(
                backend_url,
                files=files,
                data=data,
                headers=headers,
                timeout=1800  # 30分钟超时
            )
            response.raise_for_status()
            
            result = response.json()
            logger.info(f"后端处理成功: {filename}")
            return result
            
    except requests.exceptions.Timeout:
        logger.error(f"后端请求超时 - 文件: {filename}")
        raise HTTPException(status_code=504, detail="后端服务处理超时")
    except requests.exceptions.RequestException as e:
        logger.error(f"后端请求失败 - 文件: {filename}, 错误: {str(e)}", exc_info=True)
        raise HTTPException(status_code=502, detail=f"后端服务请求失败: {str(e)}")
    except Exception as e:
        logger.error(f"转发请求异常 - 文件: {filename}, 错误: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"转发请求失败: {str(e)}")
    finally:
        # 清理临时文件
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
                logger.info(f"临时文件已清理: {file_path}")
        except Exception as e:
            logger.warning(f"清理临时文件失败: {file_path}, 错误: {str(e)}")


@app.get("/")
async def root():
    """根路径，返回服务信息"""
    return {
        "service": "MinerU 转发服务",
        "version": "1.0.0",
        "backend_url": MINERU_VLM_URL,
        "workspace": WORKSPACE_ROOT
    }


@app.post("/mineru", response_model=MinerUResponse)
async def mineru_endpoint(
    body: MinerURequest, 
    request: Request,
    authorization: Optional[str] = Header(None)
):
    """
    MinerU PDF 转发接口
    
    支持两种方式：
    1. pdf_url: 从 URL 下载 PDF
    2. pdf_path: 使用本地文件路径
    
    处理流程：
    1. 下载或复制 PDF 到临时目录
    2. 转发到后端 MinerU 服务（透传 Authorization 和 IP）
    3. 返回后端响应
    """
    client_ip = get_client_ip(request)
    logger.info(f"[API /mineru] IP: {client_ip}, pdf_url={body.pdf_url}, pdf_path={body.pdf_path}")
    
    # 验证参数
    if not body.pdf_url and not body.pdf_path:
        raise HTTPException(status_code=400, detail="必须提供 pdf_url 或 pdf_path")
    
    if body.pdf_url and body.pdf_path:
        raise HTTPException(status_code=400, detail="只能提供 pdf_url 或 pdf_path 其中一个")
    
    try:
        # 生成唯一的临时文件名
        temp_filename = f"{uuid.uuid4().hex}.pdf"
        temp_pdf_path = os.path.join(WORKSPACE_TEMP, temp_filename)
        
        # 处理本地文件或 URL
        if body.pdf_path:
            if not os.path.exists(body.pdf_path):
                raise HTTPException(status_code=404, detail=f"文件不存在: {body.pdf_path}")
            
            if not body.pdf_path.lower().endswith('.pdf'):
                raise HTTPException(status_code=400, detail="只支持 PDF 文件")
            
            filename = body.pdf_filename or os.path.basename(body.pdf_path)
            shutil.copy2(body.pdf_path, temp_pdf_path)
            logger.info(f"本地文件已复制: {body.pdf_path} -> {temp_pdf_path}")
        else:
            filename = body.pdf_filename or get_filename_from_url(body.pdf_url)
            download_pdf(body.pdf_url, temp_pdf_path)
        
        # 使用线程池异步转发到后端
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            executor,
            forward_to_backend,
            temp_pdf_path,
            filename,
            client_ip,
            authorization,
            body.vlm_url,
            body.backend,
            body.lang,
            body.formula,
            body.table
        )
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[API /mineru] 处理失败, IP: {client_ip}, 错误: {str(e)}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"处理请求失败: {str(e)}")


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
    authorization: Optional[str] = Header(None)
):
    """
    通过文件上传方式转发 PDF
    
    处理流程：
    1. 接收上传的文件保存到临时目录
    2. 转发到后端 MinerU 服务（透传 Authorization 和 IP）
    3. 返回后端响应
    """
    client_ip = get_client_ip(request)
    logger.info(f"[API /file_mineru] IP: {client_ip}, File: {file.filename}")
    
    # 验证文件类型
    if not file.filename:
        raise HTTPException(status_code=400, detail="未提供文件")
    
    if not file.filename.lower().endswith('.pdf'):
        raise HTTPException(status_code=400, detail="只支持 PDF 文件")

    filename = pdf_filename or file.filename

    try:
        # 生成唯一的临时文件名
        temp_filename = f"{uuid.uuid4().hex}.pdf"
        temp_pdf_path = os.path.join(WORKSPACE_TEMP, temp_filename)
        
        # 保存上传的文件
        try:
            logger.info(f"保存上传文件: {filename} -> {temp_pdf_path}")
            with open(temp_pdf_path, "wb") as buffer:
                shutil.copyfileobj(file.file, buffer)
            logger.info(f"文件保存成功: {temp_pdf_path}")
        finally:
            file.file.close()
        
        # 使用线程池异步转发到后端
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            executor,
            forward_to_backend,
            temp_pdf_path,
            filename,
            client_ip,
            authorization,
            vlm_url,
            backend,
            lang,
            formula,
            table
        )
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[API /file_mineru] 处理失败, IP: {client_ip}, 文件: {file.filename}, 错误: {str(e)}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"处理请求失败: {str(e)}")


@app.api_route("/files/{path:path}", methods=["GET", "POST"])
async def download_file(path: str, request: Request):
    """
    文件下载转发接口（支持 GET 和 POST）
    
    将文件下载请求转发到后端 MinerU 服务
    示例: /files/output/6185e4983f3b150745fd25d09cf15e41/6185e4983f3b150745fd25d09cf15e41/vlm/6185e4983f3b150745fd25d09cf15e41_model.json
    将转发到: http://10.104.255.37:30010/files/output/.../...
    """
    client_ip = get_client_ip(request)
    logger.info(f"[API /files] Method: {request.method}, IP: {client_ip}, Path: {path}")
    
    try:
        # 构建后端文件下载 URL
        backend_url = f"{MINERU_VLM_URL.rstrip('/')}/files/{path}"
        logger.info(f"转发文件下载请求到: {backend_url}")
        
        # 准备请求头（透传 IP）
        headers = {
            'X-Forwarded-For': client_ip,
            'X-Real-IP': client_ip
        }
        
        # 转发 Authorization 头（如果有）
        if request.headers.get('Authorization'):
            headers['Authorization'] = request.headers.get('Authorization')
        
        # 根据请求方法选择对应的 requests 方法
        if request.method == "POST":
            response = requests.post(
                backend_url,
                headers=headers,
                stream=True,
                timeout=300
            )
        else:
            response = requests.get(
                backend_url,
                headers=headers,
                stream=True,
                timeout=300
            )
        response.raise_for_status()
        
        # 提取文件名
        filename = os.path.basename(path)
        content_type = response.headers.get('Content-Type', 'application/octet-stream')
        
        # 流式返回文件内容
        def iterfile():
            try:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        yield chunk
            finally:
                response.close()
        
        logger.info(f"文件下载成功: {filename}")
        return StreamingResponse(
            iterfile(),
            media_type=content_type,
            headers={
                'Content-Disposition': f'attachment; filename="{filename}"'
            }
        )
        
    except requests.exceptions.RequestException as e:
        logger.error(f"[API /files] 下载失败, IP: {client_ip}, Path: {path}, 错误: {str(e)}")
        raise HTTPException(status_code=502, detail=f"文件下载失败: {str(e)}")
    except Exception as e:
        logger.error(f"[API /files] 处理失败, IP: {client_ip}, Path: {path}, 错误: {str(e)}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"处理请求失败: {str(e)}")


if __name__ == "__main__":
    uvicorn.run("services.apis_forward:app", host=SERVER_HOST, port=SERVER_PORT, reload=True)

