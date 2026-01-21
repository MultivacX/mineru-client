# 数据库功能使用说明

## 概述

本服务已集成 SQLite 数据库，用于管理 API Keys 和记录使用日志。

## 数据库位置

数据库文件位于: `workspace/ocr_service.db`

## 数据库表结构

### 1. api_keys 表
存储 API Key 信息：

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER | 主键，自增 |
| api_key | TEXT | API Key（唯一） |
| user_id | TEXT | 用户标识 |
| created_at | TIMESTAMP | 创建时间 |
| is_active | INTEGER | 是否启用（1=启用, 0=禁用） |
| description | TEXT | 描述信息 |

### 2. usage_logs 表
记录每次 API 调用：

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER | 主键，自增 |
| md5 | TEXT | 文件 MD5 |
| api_key | TEXT | 使用的 API Key |
| user_id | TEXT | 用户标识 |
| ip_address | TEXT | 客户端 IP 地址 |
| endpoint | TEXT | 调用的端点（/mineru 或 /file_mineru） |
| filename | TEXT | 原始文件名 |
| pdf_pages | INTEGER | PDF 页数 |
| md_chars | INTEGER | Markdown 文件字符数 |
| is_cached | INTEGER | 是否使用缓存（1=是, 0=否） |
| success | INTEGER | 是否成功（1=成功, 0=失败） |
| error_message | TEXT | 错误信息（如果失败） |
| created_at | TIMESTAMP | 调用时间 |

## 数据库管理工具

提供了 `manage_db.py` 脚本来管理数据库。

### 安装依赖

```bash
pip install tabulate
```

### 基本用法

```bash
# 查看帮助
python manage_db.py -h

# 列出所有 API Keys
python manage_db.py list

# 添加新的 API Key（自动生成）
python manage_db.py add user1 -d "测试用户"

# 添加新的 API Key（自定义）
python manage_db.py add user2 -k "my_custom_key_123456" -d "自定义密钥"

# 删除 API Key（通过 ID）
python manage_db.py delete 1

# 启用/禁用 API Key（通过 ID）
python manage_db.py toggle 2

# 查看使用统计（最近 20 条）
python manage_db.py stats

# 查看使用统计（最近 50 条）
python manage_db.py stats -l 50

# 查看特定用户的使用统计
python manage_db.py stats -u user1
```

## API Key 管理

### 1. 从环境变量迁移

首次启动服务时，如果数据库为空，会自动从环境变量迁移 API Keys：

```bash
# 单个 API Key（向后兼容）
export OCR_API_KEY="your_api_key_here"

# 多个 API Keys（JSON 格式）
export OCR_API_KEYS='{"user1": "key1", "user2": "key2"}'
```

### 2. 使用数据库管理

推荐使用 `manage_db.py` 脚本管理 API Keys，无需重启服务即可生效。

### 3. 鉴权逻辑

- 如果数据库中 **没有任何启用的 API Key**，则 **不进行鉴权**（所有请求都可访问）
- 如果数据库中 **有启用的 API Key**，则 **必须提供有效的 Bearer Token**

### 4. API 调用示例

```bash
# 使用 API Key 调用
curl -X POST "http://localhost:8081/mineru" \
  -H "Authorization: Bearer your_api_key_here" \
  -H "Content-Type: application/json" \
  -d '{"pdf_url": "https://example.com/document.pdf"}'
```

## 使用日志查询

### 使用管理工具查询

```bash
# 查看最近 20 条使用记录
python manage_db.py stats

# 查看最近 100 条使用记录
python manage_db.py stats -l 100

# 查看特定用户的使用记录
python manage_db.py stats -u user1
```

### 直接查询数据库

也可以使用 SQLite 命令行工具直接查询：

```bash
sqlite3 workspace/ocr_service.db

# 查询最近 10 条记录
SELECT * FROM usage_logs ORDER BY created_at DESC LIMIT 10;

# 按用户统计
SELECT user_id, COUNT(*) as count, SUM(pdf_pages) as total_pages 
FROM usage_logs 
WHERE user_id IS NOT NULL 
GROUP BY user_id;

# 查询缓存命中率
SELECT 
    COUNT(*) as total,
    SUM(CASE WHEN is_cached = 1 THEN 1 ELSE 0 END) as cached,
    ROUND(SUM(CASE WHEN is_cached = 1 THEN 1 ELSE 0 END) * 100.0 / COUNT(*), 2) as cache_rate
FROM usage_logs;
```

## 记录的信息

每次调用 `/mineru` 或 `/file_mineru` 端点时，会自动记录以下信息：

1. **文件信息**
   - MD5 哈希值
   - 原始文件名
   - PDF 页数

2. **处理结果**
   - Markdown 文件字符数
   - 是否使用了缓存
   - 是否成功
   - 错误信息（如果失败）

3. **用户信息**
   - API Key（原始 token）
   - User ID（从 API Key 映射）
   - IP 地址

4. **调用信息**
   - 端点路径
   - 调用时间戳

## Kylin Linux 安装 SQLite

Kylin Linux Advanced Server V10 (Lance) 安装 SQLite：

### 方法 1: 使用 yum 安装（推荐）

```bash
sudo yum install sqlite sqlite-devel
```

### 方法 2: 使用 dnf 安装

```bash
sudo dnf install sqlite sqlite-devel
```

### 方法 3: 从源码编译

1. 下载源码
```bash
wget https://www.sqlite.org/2025/sqlite-autoconf-3480000.tar.gz
tar xvfz sqlite-autoconf-3480000.tar.gz
cd sqlite-autoconf-3480000
```

2. 编译安装
```bash
./configure --prefix=/usr/local
make
sudo make install
```

### SQLite 官方下载地址

- 官网：https://www.sqlite.org/download.html
- 源码包（2025）：https://www.sqlite.org/2025/sqlite-autoconf-3480000.tar.gz
- 预编译二进制：https://www.sqlite.org/2025/sqlite-tools-linux-x64-3480000.zip

### 验证安装

```bash
sqlite3 --version
python3 -c "import sqlite3; print(sqlite3.sqlite_version)"
```

## 数据备份

定期备份数据库文件：

```bash
# 备份数据库
cp workspace/ocr_service.db workspace/ocr_service.db.backup.$(date +%Y%m%d)

# 或使用 SQLite 的备份命令
sqlite3 workspace/ocr_service.db ".backup 'workspace/ocr_service.db.backup'"
```

## 性能优化

数据库已创建以下索引以优化查询性能：

- `idx_api_key`: api_keys 表的 api_key 字段
- `idx_usage_md5`: usage_logs 表的 md5 字段
- `idx_usage_api_key`: usage_logs 表的 api_key 字段
- `idx_usage_created_at`: usage_logs 表的 created_at 字段

## 故障排查

### 问题 1: 数据库文件不存在

**解决方法**: 启动服务会自动创建数据库文件。

### 问题 2: 权限错误

**解决方法**: 确保运行服务的用户对 `workspace` 目录有读写权限。

```bash
chmod 755 workspace
chmod 644 workspace/ocr_service.db  # 如果文件已存在
```

### 问题 3: API Key 验证失败

**解决方法**:
1. 检查 API Key 是否在数据库中: `python manage_db.py list`
2. 检查 API Key 是否启用（is_active = 1）
3. 确保请求头格式正确: `Authorization: Bearer <token>`

## 日志文件

除了数据库记录，服务还会生成日志文件：

- 位置: `workspace/ocr_service.log`
- 包含详细的请求和错误信息
- 按日期自动轮转（如果配置）
