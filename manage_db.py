#!/usr/bin/env python3
"""
数据库管理工具
用于管理 API Keys 和查询使用日志
"""

import os
import sys
import sqlite3
import argparse
import secrets
from datetime import datetime
from tabulate import tabulate

# 获取数据库路径
WORKSPACE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), 'workspace'))
DB_PATH = os.path.join(WORKSPACE_ROOT, 'ocr_service.db')


def get_db_connection():
    """获取数据库连接"""
    if not os.path.exists(DB_PATH):
        print(f"错误: 数据库文件不存在: {DB_PATH}")
        print("请先启动服务以初始化数据库")
        sys.exit(1)
    return sqlite3.connect(DB_PATH)


def list_api_keys():
    """列出所有 API Keys"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT id, api_key, user_id, created_at, is_active, description 
        FROM api_keys 
        ORDER BY id
    ''')
    
    rows = cursor.fetchall()
    conn.close()
    
    if not rows:
        print("没有找到任何 API Keys")
        return
    
    headers = ['ID', 'API Key', 'User ID', 'Created At', 'Active', 'Description']
    table_data = []
    for row in rows:
        id_, api_key, user_id, created_at, is_active, description = row
        # 只显示 API Key 的前 10 位和后 4 位
        masked_key = f"{api_key[:10]}...{api_key[-4:]}" if len(api_key) > 14 else api_key
        active_str = "✓" if is_active else "✗"
        table_data.append([id_, masked_key, user_id, created_at, active_str, description or ''])
    
    print(tabulate(table_data, headers=headers, tablefmt='grid'))


def add_api_key(user_id: str, description: str = None, custom_key: str = None):
    """添加新的 API Key"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # 生成或使用自定义的 API Key
    if custom_key:
        api_key = custom_key
    else:
        api_key = secrets.token_urlsafe(32)
    
    try:
        cursor.execute(
            'INSERT INTO api_keys (api_key, user_id, description) VALUES (?, ?, ?)',
            (api_key, user_id, description)
        )
        conn.commit()
        print(f"✓ API Key 添加成功")
        print(f"  User ID: {user_id}")
        print(f"  API Key: {api_key}")
        if description:
            print(f"  Description: {description}")
        print(f"\n请妥善保存此 API Key，不会再次显示完整内容")
    except sqlite3.IntegrityError:
        print(f"✗ 错误: API Key 已存在")
    finally:
        conn.close()


def delete_api_key(key_id: int):
    """删除 API Key"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # 先查询是否存在
    cursor.execute('SELECT user_id FROM api_keys WHERE id = ?', (key_id,))
    result = cursor.fetchone()
    
    if not result:
        print(f"✗ 错误: 未找到 ID 为 {key_id} 的 API Key")
        conn.close()
        return
    
    # 删除
    cursor.execute('DELETE FROM api_keys WHERE id = ?', (key_id,))
    conn.commit()
    conn.close()
    
    print(f"✓ API Key (ID: {key_id}, User: {result[0]}) 已删除")


def toggle_api_key(key_id: int):
    """启用/禁用 API Key"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # 查询当前状态
    cursor.execute('SELECT user_id, is_active FROM api_keys WHERE id = ?', (key_id,))
    result = cursor.fetchone()
    
    if not result:
        print(f"✗ 错误: 未找到 ID 为 {key_id} 的 API Key")
        conn.close()
        return
    
    user_id, is_active = result
    new_status = 0 if is_active else 1
    
    # 切换状态
    cursor.execute('UPDATE api_keys SET is_active = ? WHERE id = ?', (new_status, key_id))
    conn.commit()
    conn.close()
    
    status_str = "启用" if new_status else "禁用"
    print(f"✓ API Key (ID: {key_id}, User: {user_id}) 已{status_str}")


def show_usage_stats(limit: int = 20, user_id: str = None):
    """显示使用统计"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    where_clause = ""
    params = []
    
    if user_id:
        where_clause = "WHERE user_id = ?"
        params.append(user_id)
    
    # 查询最近的使用记录
    query = f'''
        SELECT id, md5, user_id, ip_address, endpoint, filename, 
               pdf_pages, md_chars, is_cached, success, created_at
        FROM usage_logs 
        {where_clause}
        ORDER BY created_at DESC 
        LIMIT ?
    '''
    params.append(limit)
    
    cursor.execute(query, params)
    rows = cursor.fetchall()
    
    if not rows:
        print("没有找到使用记录")
        conn.close()
        return
    
    headers = ['ID', 'MD5', 'User', 'IP', 'Endpoint', 'Filename', 'Pages', 'Chars', 'Cached', 'Success', 'Time']
    table_data = []
    
    for row in rows:
        id_, md5, uid, ip, endpoint, filename, pages, chars, cached, success, created = row
        # 缩短显示
        md5_short = f"{md5[:8]}..." if md5 else "N/A"
        filename_short = filename[:20] + "..." if filename and len(filename) > 20 else filename
        cached_str = "✓" if cached else ""
        success_str = "✓" if success else "✗"
        
        table_data.append([
            id_, md5_short, uid or 'N/A', ip, endpoint, filename_short,
            pages, chars, cached_str, success_str, created
        ])
    
    print(tabulate(table_data, headers=headers, tablefmt='grid'))
    
    # 显示汇总统计
    print("\n=== 汇总统计 ===")
    
    summary_query = f'''
        SELECT 
            COUNT(*) as total_requests,
            SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) as successful,
            SUM(CASE WHEN is_cached = 1 THEN 1 ELSE 0 END) as cached,
            SUM(pdf_pages) as total_pages,
            SUM(md_chars) as total_chars
        FROM usage_logs
        {where_clause}
    '''
    
    cursor.execute(summary_query, params[:-1] if user_id else [])
    stats = cursor.fetchone()
    
    if stats:
        total, successful, cached, total_pages, total_chars = stats
        print(f"总请求数: {total}")
        print(f"成功请求: {successful} ({successful*100//total if total else 0}%)")
        print(f"缓存命中: {cached} ({cached*100//total if total else 0}%)")
        print(f"总页数: {total_pages}")
        print(f"总字符数: {total_chars}")
    
    conn.close()


def main():
    parser = argparse.ArgumentParser(description='MinerU OCR 服务数据库管理工具')
    subparsers = parser.add_subparsers(dest='command', help='可用命令')
    
    # 列出 API Keys
    subparsers.add_parser('list', help='列出所有 API Keys')
    
    # 添加 API Key
    add_parser = subparsers.add_parser('add', help='添加新的 API Key')
    add_parser.add_argument('user_id', help='用户 ID')
    add_parser.add_argument('-d', '--description', help='描述信息')
    add_parser.add_argument('-k', '--key', help='自定义 API Key（不指定则自动生成）')
    
    # 删除 API Key
    delete_parser = subparsers.add_parser('delete', help='删除 API Key')
    delete_parser.add_argument('id', type=int, help='API Key ID')
    
    # 启用/禁用 API Key
    toggle_parser = subparsers.add_parser('toggle', help='启用/禁用 API Key')
    toggle_parser.add_argument('id', type=int, help='API Key ID')
    
    # 查看使用统计
    stats_parser = subparsers.add_parser('stats', help='查看使用统计')
    stats_parser.add_argument('-l', '--limit', type=int, default=20, help='显示记录数（默认 20）')
    stats_parser.add_argument('-u', '--user', help='筛选特定用户')
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        return
    
    if args.command == 'list':
        list_api_keys()
    elif args.command == 'add':
        add_api_key(args.user_id, args.description, args.key)
    elif args.command == 'delete':
        delete_api_key(args.id)
    elif args.command == 'toggle':
        toggle_api_key(args.id)
    elif args.command == 'stats':
        show_usage_stats(args.limit, args.user)


if __name__ == '__main__':
    main()
