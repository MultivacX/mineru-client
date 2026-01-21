#!/bin/bash
# API 使用示例脚本

# 配置
API_URL="http://localhost:8081"
API_KEY="your_api_key_here"

echo "================================"
echo "MinerU OCR 服务 API 使用示例"
echo "================================"

# 检查是否提供了 API Key
if [ "$API_KEY" = "your_api_key_here" ]; then
    echo ""
    echo "警告: 请先设置 API_KEY 变量"
    echo "编辑此脚本，将 API_KEY 设置为您的实际密钥"
    echo ""
    echo "如果服务未启用鉴权（数据库中没有 API Key），"
    echo "可以不设置 API_KEY，直接使用"
    echo ""
    USE_AUTH=false
else
    USE_AUTH=true
fi

# 示例 1: 通过 URL 处理 PDF
example_1() {
    echo ""
    echo "[示例 1] 通过 URL 处理 PDF"
    echo "----------------------------"
    
    if [ "$USE_AUTH" = true ]; then
        curl -X POST "${API_URL}/mineru" \
          -H "Authorization: Bearer ${API_KEY}" \
          -H "Content-Type: application/json" \
          -d '{
            "pdf_url": "https://example.com/document.pdf",
            "pdf_filename": "my_document.pdf"
          }'
    else
        curl -X POST "${API_URL}/mineru" \
          -H "Content-Type: application/json" \
          -d '{
            "pdf_url": "https://example.com/document.pdf",
            "pdf_filename": "my_document.pdf"
          }'
    fi
}

# 示例 2: 上传本地文件
example_2() {
    echo ""
    echo "[示例 2] 上传本地文件"
    echo "----------------------------"
    
    # 替换为实际的文件路径
    LOCAL_FILE="./test.pdf"
    
    if [ ! -f "$LOCAL_FILE" ]; then
        echo "错误: 文件不存在: $LOCAL_FILE"
        echo "请将 LOCAL_FILE 变量设置为实际的 PDF 文件路径"
        return
    fi
    
    if [ "$USE_AUTH" = true ]; then
        curl -X POST "${API_URL}/file_mineru" \
          -H "Authorization: Bearer ${API_KEY}" \
          -F "file=@${LOCAL_FILE}" \
          -F "pdf_filename=my_uploaded_doc.pdf"
    else
        curl -X POST "${API_URL}/file_mineru" \
          -F "file=@${LOCAL_FILE}" \
          -F "pdf_filename=my_uploaded_doc.pdf"
    fi
}

# 示例 3: 使用本地文件路径（服务器端文件）
example_3() {
    echo ""
    echo "[示例 3] 使用本地文件路径"
    echo "----------------------------"
    
    # 替换为服务器上的实际文件路径
    SERVER_FILE="/path/to/server/document.pdf"
    
    if [ "$USE_AUTH" = true ]; then
        curl -X POST "${API_URL}/mineru" \
          -H "Authorization: Bearer ${API_KEY}" \
          -H "Content-Type: application/json" \
          -d "{
            \"pdf_path\": \"${SERVER_FILE}\",
            \"pdf_filename\": \"server_document.pdf\"
          }"
    else
        curl -X POST "${API_URL}/mineru" \
          -H "Content-Type: application/json" \
          -d "{
            \"pdf_path\": \"${SERVER_FILE}\",
            \"pdf_filename\": \"server_document.pdf\"
          }"
    fi
}

# 示例 4: 自定义 MinerU 参数
example_4() {
    echo ""
    echo "[示例 4] 自定义 MinerU 参数"
    echo "----------------------------"
    
    if [ "$USE_AUTH" = true ]; then
        curl -X POST "${API_URL}/mineru" \
          -H "Authorization: Bearer ${API_KEY}" \
          -H "Content-Type: application/json" \
          -d '{
            "pdf_url": "https://example.com/document.pdf",
            "vlm_url": "http://custom-vlm-server:8080",
            "backend": "vlm-http-client",
            "lang": "ch",
            "formula": true,
            "table": true
          }'
    else
        curl -X POST "${API_URL}/mineru" \
          -H "Content-Type: application/json" \
          -d '{
            "pdf_url": "https://example.com/document.pdf",
            "vlm_url": "http://custom-vlm-server:8080",
            "backend": "vlm-http-client",
            "lang": "ch",
            "formula": true,
            "table": true
          }'
    fi
}

# 示例 5: 查询处理状态
example_5() {
    echo ""
    echo "[示例 5] 查询处理状态"
    echo "----------------------------"
    
    # 替换为实际的 MD5 值
    MD5="your_file_md5_hash"
    
    curl -X GET "${API_URL}/status/${MD5}"
}

# 示例 6: 列出所有任务
example_6() {
    echo ""
    echo "[示例 6] 列出所有任务"
    echo "----------------------------"
    
    echo "列出所有任务:"
    curl -X GET "${API_URL}/list?type=all"
    
    echo ""
    echo "仅列出输入任务:"
    curl -X GET "${API_URL}/list?type=input"
    
    echo ""
    echo "仅列出输出任务:"
    curl -X GET "${API_URL}/list?type=output"
}

# 主菜单
show_menu() {
    echo ""
    echo "请选择要运行的示例:"
    echo "  1) 通过 URL 处理 PDF"
    echo "  2) 上传本地文件"
    echo "  3) 使用本地文件路径（服务器端）"
    echo "  4) 自定义 MinerU 参数"
    echo "  5) 查询处理状态"
    echo "  6) 列出所有任务"
    echo "  0) 退出"
    echo ""
    read -p "选择 (0-6): " choice
    
    case $choice in
        1) example_1 ;;
        2) example_2 ;;
        3) example_3 ;;
        4) example_4 ;;
        5) example_5 ;;
        6) example_6 ;;
        0) echo "退出"; exit 0 ;;
        *) echo "无效选择" ;;
    esac
}

# 如果没有参数，显示菜单
if [ $# -eq 0 ]; then
    while true; do
        show_menu
    done
else
    # 如果有参数，直接运行指定示例
    case $1 in
        1) example_1 ;;
        2) example_2 ;;
        3) example_3 ;;
        4) example_4 ;;
        5) example_5 ;;
        6) example_6 ;;
        *) echo "用法: $0 [1-6]" ;;
    esac
fi
