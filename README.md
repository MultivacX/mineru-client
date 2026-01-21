0. API 地址：http://localhost:8081/mineru
- http://10.104.129.136:8081/mineru
- http://10.104.129.137:8081/mineru
- http://10.104.129.138:8081/mineru

1. 准备文件
- 将 miniconda 安装文件下载到本目录 https://repo.anaconda.com/miniconda/Miniconda3-py310_25.11.1-1-Linux-x86_64.sh
- 将本目录下所有文件上传到服务器的 /root/spic_dt_llm_dev 目录下

2. 安装 conda
```sh
mkdir -p ~/miniconda3
# wget https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh -O ~/miniconda3/miniconda.sh
cp /root/spic_dt_llm_dev/Miniconda3-py310_25.11.1-1-Linux-x86_64.sh ~/miniconda3/miniconda.sh
bash ~/miniconda3/miniconda.sh -b -u -p ~/miniconda3
rm ~/miniconda3/miniconda.sh
```

3. 安装 python 库
```sh
source ~/miniconda3/bin/activate
conda init --all

conda create -n mineru-client --clone base --offline
conda activate mineru-client
pip install mineru fastapi uvicorn starlette pydantic pydantic_core filelock fitz frontend tools tabulate -i  http://10.104.7.78:8008/simple --trusted-host=10.104.7.78
```


4. 启动服务
```sh
cd /root/spic_dt_llm_dev
conda activate mineru-client

# uvicorn services.apis_ocr:app --reload --host 0.0.0.0 --port 8081

# 生产环境
pkill -f "uvicorn services.apis_ocr"
sleep 2
nohup uvicorn services.apis_ocr:app --host 0.0.0.0 --port 8081 > uvicorn.log 2>&1 &
```

5. 在服务器上测试本地文件，返回值见 workspace/公开-设备买卖合同.json
```sh
curl -X POST "http://localhost:8081/mineru" \
  -H "Content-Type: application/json" \
  -d '{
    "pdf_path": "/root/spic_dt_llm_dev/workspace/公开-设备买卖合同.pdf"
  }'
```

6. API Key 配置（可选）
```sh
# 方式1：配置多个用户的 API Key（推荐）
export OCR_API_KEYS='{
  "user1": "key_abc123",
  "user2": "key_xyz789",
  "admin": "key_admin456"
}'

# 方式2：配置单个 API Key（向后兼容）
export OCR_API_KEY="your-secret-key"

# 如果不设置以上环境变量，则不启用鉴权
```

7. 调用方式

**基础调用示例（未启用鉴权）**

- 通过文件 URL 处理
```sh
curl -X POST "http://localhost:8081/mineru" \
   -H "Content-Type: application/json" \
   -d '{
     "pdf_url": "https://example.com/sample.pdf"
   }'
```
```python
import requests

response = requests.post(
    'http://localhost:8081/mineru',
    json={'pdf_url': 'https://example.com/sample.pdf'}
)
```

- 在服务器上处理本地文件
```sh
curl -X POST "http://localhost:8081/mineru" \
   -H "Content-Type: application/json" \
   -d '{
     "pdf_path": "/xxx/document.pdf"
   }'
```
```python
import requests

response = requests.post(
    'http://localhost:8081/mineru',
    json={'pdf_path': '/path/to/local/document.pdf'}
)
```

- 通过文件上传处理
```sh
curl -X POST "http://localhost:8081/file_mineru" \
   -F "file=@/path/to/local/document.pdf" \
   -F "pdf_filename=custom_name.pdf"
```
```python
import requests

with open('/path/to/local/document.pdf', 'rb') as f:
    response = requests.post(
        'http://localhost:8081/file_mineru',
        files={'file': f},
        data={'pdf_filename': 'custom_name.pdf'}
    )
```

**带 Bearer Token 鉴权的调用示例（启用鉴权后）**

- 通过文件 URL 处理
```sh
curl -X POST "http://localhost:8081/mineru" \
   -H "Authorization: Bearer key_abc123" \
   -H "Content-Type: application/json" \
   -d '{
     "pdf_url": "https://example.com/sample.pdf"
   }'
```
```python
import requests

headers = {
    'Authorization': 'Bearer key_abc123'
}

response = requests.post(
    'http://localhost:8081/mineru',
    headers=headers,
    json={'pdf_url': 'https://example.com/sample.pdf'}
)
```

- 在服务器上处理本地文件
```sh
curl -X POST "http://localhost:8081/mineru" \
   -H "Authorization: Bearer key_abc123" \
   -H "Content-Type: application/json" \
   -d '{
     "pdf_path": "/xxx/document.pdf"
   }'
```
```python
import requests

headers = {
    'Authorization': 'Bearer key_abc123'
}

response = requests.post(
    'http://localhost:8081/mineru',
    headers=headers,
    json={'pdf_path': '/path/to/local/document.pdf'}
)
```

- 通过文件上传处理
```sh
curl -X POST "http://localhost:8081/file_mineru" \
   -H "Authorization: Bearer key_abc123" \
   -F "file=@/path/to/local/document.pdf" \
   -F "pdf_filename=custom_name.pdf"
```
```python
import requests

headers = {
    'Authorization': 'Bearer key_abc123'
}

with open('/path/to/local/document.pdf', 'rb') as f:
    response = requests.post(
        'http://localhost:8081/file_mineru',
        headers=headers,
        files={'file': f},
        data={'pdf_filename': 'custom_name.pdf'}
    )
```

**高级参数配置**
```sh
curl -X POST "http://localhost:8081/mineru" \
   -H "Authorization: Bearer key_abc123" \
   -H "Content-Type: application/json" \
   -d '{
     "pdf_url": "https://example.com/sample.pdf",
     "vlm_url": "http://custom-vlm:30010",
     "backend": "vlm-http-client",
     "lang": "en",
     "formula": true,
     "table": true
   }'
```

## 数据库功能

本服务集成了 SQLite 数据库，用于管理 API Keys 和记录使用日志。

### 功能特性

1. **API Key 管理**
   - 从数据库读取和验证 API Keys
   - 支持启用/禁用 API Keys
   - 自动从环境变量迁移到数据库

2. **使用日志记录**
   - 记录每次 API 调用的详细信息
   - 包括：PDF 页数、MD 字符数、IP 地址、是否使用缓存等
   - 支持按用户、时间等维度查询统计

### 数据库管理工具

使用 `manage_db.py` 脚本管理数据库：

```sh
# 列出所有 API Keys
python manage_db.py list

# 添加新的 API Key（自动生成）
python manage_db.py add user1 -d "测试用户"

# 添加新的 API Key（自定义）
python manage_db.py add user2 -k "my_custom_key_123" -d "自定义密钥"

# 删除 API Key
python manage_db.py delete 1

# 启用/禁用 API Key
python manage_db.py toggle 2

# 查看使用统计
python manage_db.py stats

# 查看特定用户的使用统计
python manage_db.py stats -u user1
```

### Kylin Linux 安装 SQLite

在 Kylin Linux Advanced Server V10 上安装 SQLite：

```sh
# 方法1: 使用 yum（推荐）
sudo yum install sqlite sqlite-devel

# 方法2: 使用 dnf
sudo dnf install sqlite sqlite-devel
```

**官方下载地址**：
- SQLite 官网：https://www.sqlite.org/download.html
- 源码包：https://www.sqlite.org/2025/sqlite-autoconf-3480000.tar.gz
- 预编译二进制：https://www.sqlite.org/2025/sqlite-tools-linux-x64-3480000.zip

详细说明请参考 [DATABASE_USAGE.md](DATABASE_USAGE.md)