#!/usr/bin/env python3
"""
测试数据库功能
"""

import os
import sys
import sqlite3

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 设置测试环境
os.environ['WORKSPACE_ROOT'] = os.path.join(os.path.dirname(__file__), 'workspace')

from services.apis_ocr import (
    init_database, 
    migrate_api_keys_from_env,
    log_usage,
    DB_PATH,
    WORKSPACE_ROOT
)

def test_database():
    """测试数据库功能"""
    print("=" * 60)
    print("数据库功能测试")
    print("=" * 60)
    
    # 确保工作目录存在
    os.makedirs(WORKSPACE_ROOT, exist_ok=True)
    
    # 1. 测试数据库初始化
    print("\n[1] 测试数据库初始化...")
    init_database()
    assert os.path.exists(DB_PATH), "数据库文件未创建"
    print("✓ 数据库初始化成功")
    
    # 2. 测试表结构
    print("\n[2] 检查表结构...")
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # 检查 api_keys 表
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='api_keys'")
    assert cursor.fetchone() is not None, "api_keys 表不存在"
    print("✓ api_keys 表已创建")
    
    # 检查 usage_logs 表
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='usage_logs'")
    assert cursor.fetchone() is not None, "usage_logs 表不存在"
    print("✓ usage_logs 表已创建")
    
    conn.close()
    
    # 3. 测试添加 API Key
    print("\n[3] 测试添加 API Key...")
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    test_key = "test_key_123456"
    test_user = "test_user"
    
    cursor.execute(
        'INSERT INTO api_keys (api_key, user_id, description) VALUES (?, ?, ?)',
        (test_key, test_user, '测试密钥')
    )
    conn.commit()
    
    # 验证插入
    cursor.execute('SELECT user_id FROM api_keys WHERE api_key = ?', (test_key,))
    result = cursor.fetchone()
    assert result is not None and result[0] == test_user, "API Key 插入失败"
    print(f"✓ API Key 添加成功: {test_user}")
    
    conn.close()
    
    # 4. 测试使用日志记录
    print("\n[4] 测试使用日志记录...")
    
    log_usage(
        md5="test_md5_hash",
        api_key=test_key,
        user_id=test_user,
        ip_address="127.0.0.1",
        endpoint="/mineru",
        filename="test.pdf",
        pdf_pages=10,
        md_chars=5000,
        is_cached=False,
        success=True
    )
    
    # 验证记录
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) FROM usage_logs WHERE md5 = ?', ("test_md5_hash",))
    count = cursor.fetchone()[0]
    assert count > 0, "使用日志记录失败"
    print(f"✓ 使用日志记录成功")
    
    # 5. 测试查询统计
    print("\n[5] 测试查询统计...")
    cursor.execute('''
        SELECT COUNT(*) as total, 
               SUM(pdf_pages) as total_pages, 
               SUM(md_chars) as total_chars
        FROM usage_logs
    ''')
    stats = cursor.fetchone()
    total, total_pages, total_chars = stats
    
    print(f"  总请求数: {total}")
    print(f"  总页数: {total_pages}")
    print(f"  总字符数: {total_chars}")
    
    conn.close()
    
    # 6. 测试索引
    print("\n[6] 检查索引...")
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("SELECT name FROM sqlite_master WHERE type='index'")
    indexes = [row[0] for row in cursor.fetchall()]
    
    expected_indexes = ['idx_api_key', 'idx_usage_md5', 'idx_usage_api_key', 'idx_usage_created_at']
    for idx in expected_indexes:
        assert idx in indexes, f"索引 {idx} 不存在"
        print(f"✓ 索引 {idx} 已创建")
    
    conn.close()
    
    print("\n" + "=" * 60)
    print("所有测试通过！")
    print("=" * 60)
    print(f"\n数据库位置: {DB_PATH}")
    print("\n可以使用以下命令管理数据库:")
    print("  python manage_db.py list       # 列出 API Keys")
    print("  python manage_db.py stats      # 查看使用统计")


if __name__ == '__main__':
    try:
        test_database()
    except Exception as e:
        print(f"\n✗ 测试失败: {str(e)}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
