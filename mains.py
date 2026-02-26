import requests
import json
import time
import random
import hashlib
import re
import unicodedata
from datetime import datetime
from collections import defaultdict
import os

# -------------------------- 全局配置（Win7 32位+咪咕最新接口） --------------------------
# 关闭SSL警告
requests.packages.urllib3.disable_warnings()

# 核心配置（单线程+长间隔，避免风控）
thread_mum = 1  # 强制单线程，降低风控概率
DELAY = 3  # 每个频道请求间隔3秒，适配咪咕风控
TIMEOUT = 30  # 超时时间延长到30秒

# 最新咪咕H5请求头（2026年可用版本）
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 6.1; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/90.0.4430.212 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Referer": "https://www.miguvideo.com/",
    "Origin": "https://www.miguvideo.com",
    "sec-ch-ua": '"Chromium";v="90", "Windows NT 6.1";v="10.0"',
    "sec-ch-ua-mobile": "?0",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-site",
    "Connection": "keep-alive",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache"
}

# 分类配置（保留）
lives = ['热门', '央视', '卫视', '地方', '体育', '影视', '综艺', '少儿', '新闻', '教育', '熊猫', '纪实']
LIVE = {
    '热门': 'e7716fea6aa1483c80cfc10b7795fcb8',
    '体育': '7538163cdac044398cb292ecf75db4e0',
    '央视': '1ff892f2b5ab4a79be6e25b69d2f5d05',
    '卫视': '0847b3f6c08a4ca28f85ba5701268424',
    '地方': '855e9adc91b04ea18ef3f2dbd43f495b',
    '影视': '10b0d04cb23d4ac5945c4bc77c7ac44e',
    '新闻': 'c584f67ad63f4bc983c31de3a9be977c',
    '教育': 'af72267483d94275995a4498b2799ecd',
    '熊猫': 'e76e56e88fff4c11b0168f55e826445d',
    '综艺': '192a12edfef04b5eb616b878f031f32f',
    '少儿': 'fc2f5b8fd7db43ff88c4243e731ecede',
    '纪实': 'e1165138bdaa44b9a3138d74af6c6673'
}

# 输出路径
m3u_path = 'migu.m3u'
txt_path = 'migu.txt'
M3U_HEADER = '#EXTM3U\n'

# 全局变量
channels_dict = {}
processed_pids = set()
valid_channels = 0  # 有效频道计数


# -------------------------- 排序与分类函数（保留） --------------------------
def extract_cctv_number(channel_name):
    match = re.search(r'CCTV[-\s]?(\d+)', channel_name)
    if match:
        try:
            return int(match.group(1))
        except:
            return 999
    if 'CCTV' in channel_name:
        cgtn_map = {'法语': 1001, '西班牙语': 1002, '俄语': 1003, '阿拉伯语': 1004, '外语纪录': 1005}
        for k, v in cgtn_map.items():
            if k in channel_name:
                return v
        return 1000
    return 9999


def extract_panda_number(channel_name):
    zero_match = re.search(r'熊猫0(\d+)', channel_name)
    if zero_match:
        try:
            return (0, int(zero_match.group(1)))
        except:
            return (999, 999)
    normal_match = re.search(r'熊猫(\d+)', channel_name)
    if normal_match:
        try:
            return (1, int(normal_match.group(1)))
        except:
            return (999, 999)
    return (9999, 9999)


def extract_satellite_first_char(channel_name):
    if not channel_name:
        return 'z'
    return unicodedata.normalize('NFKC', channel_name[0])


def get_sort_key(channel_name):
    if 'CCTV' in channel_name:
        return (0, extract_cctv_number(channel_name), channel_name)
    if '熊猫' in channel_name:
        return (1, extract_panda_number(channel_name), channel_name)
    if '卫视' in channel_name and 'CCTV' not in channel_name:
        return (2, extract_satellite_first_char(channel_name), channel_name)
    return (3, channel_name)


def smart_classify_5_categories(channel_name):
    if channel_name in channels_dict:
        return None
    if '熊猫' in channel_name:
        return '🐼熊猫频道'
    if 'CCTV' in channel_name or 'CGTN' in channel_name:
        return '📺央视频道'
    if '卫视' in channel_name and 'CCTV' not in channel_name:
        return '📡卫视频道'
    entertainment_keywords = ['电影', '影视', '少儿', '卡通', '动漫', '综艺', '音乐', '戏曲']
    for keyword in entertainment_keywords:
        if keyword in channel_name:
            return '🎬影音娱乐'
    return '📰生活资讯'


# -------------------------- 核心请求函数（修复NoneType+更换新接口） --------------------------
def get_live_channel_list(category_id):
    """获取分类下的频道列表（修复接口请求）"""
    url = f'https://program-sc.miguvideo.com/live/v2/tv-data/{category_id}'
    try:
        resp = requests.get(
            url,
            headers=headers,
            timeout=TIMEOUT,
            verify=False
        )
        resp.raise_for_status()
        resp_json = resp.json()
        # 增加空值判断
        if not resp_json or 'body' not in resp_json or 'dataList' not in resp_json['body']:
            return []
        return resp_json['body']['dataList']
    except Exception as e:
        print(f"❌ 获取频道列表失败: {str(e)[:50]}")
        return []


def get_play_url(pid):
    """获取播放链接（更换咪咕v3接口+完善判空）"""
    global valid_channels
    # 新接口：无需复杂签名，直接请求
    url = f'https://webapi.miguvideo.com/gateway/playurl/v3/play/playurl?contId={pid}&rateType=3&xh265=true'

    try:
        time.sleep(DELAY)  # 间隔防封
        resp = requests.get(
            url,
            headers=headers,
            timeout=TIMEOUT,
            verify=False
        )
        resp.raise_for_status()
        resp_json = resp.json()

        # 逐层判空，避免NoneType错误
        if not resp_json:
            raise Exception("接口返回空数据")
        if resp_json.get('code') != '200':
            raise Exception(f"接口返回错误码: {resp_json.get('code')}")
        if 'body' not in resp_json:
            raise Exception("无body字段")
        if 'urlInfo' not in resp_json['body']:
            raise Exception("无urlInfo字段")
        if 'url' not in resp_json['body']['urlInfo']:
            raise Exception("无播放url")

        raw_url = resp_json['body']['urlInfo']['url']
        if not raw_url or raw_url == '':
            raise Exception("播放url为空")

        # 处理302跳转（简化逻辑）
        try:
            resp_redirect = requests.get(
                raw_url,
                allow_redirects=False,
                timeout=10,
                verify=False,
                headers={"User-Agent": headers["User-Agent"]}
            )
            final_url = resp_redirect.headers.get('Location', raw_url)
            if final_url.startswith('http'):
                return final_url
            return raw_url
        except:
            return raw_url

    except Exception as e:
        print(f"❌ PID {pid} 播放链接获取失败: {str(e)[:50]}")
        return None


def process_channel(channel_data):
    """处理单个频道（完善容错）"""
    global valid_channels
    try:
        # 提取核心字段并判空
        pid = channel_data.get('pID')
        ch_name = channel_data.get('name', '未知频道')
        if not pid or pid in processed_pids:
            return
        processed_pids.add(pid)

        # 获取播放链接
        playurl = get_play_url(pid)
        if not playurl:
            return

        # 处理频道名
        if "CCTV" in ch_name and "CCTV-" not in ch_name:
            ch_name = ch_name.replace("CCTV", "CCTV-")
        if "熊猫" in ch_name:
            ch_name = ch_name.replace("高清", "")

        # 分类与排序
        category = smart_classify_5_categories(ch_name)
        if category is None:
            return
        sort_key = get_sort_key(ch_name)

        # 构造输出条目
        m3u_item = f'#EXTINF:-1 group-title="{category}",{ch_name}\n{playurl}\n'
        txt_item = f"{ch_name},{playurl}\n"

        channels_dict[ch_name] = [m3u_item, txt_item, category, sort_key]
        valid_channels += 1
        print(f'✅ 成功抓取: [{category}] {ch_name}')

    except Exception as e:
        ch_name = channel_data.get('name', '未知频道')
        print(f"❌ 频道 {ch_name} 处理失败: {str(e)[:50]}")


# -------------------------- 主函数（单线程+逐分类抓取） --------------------------
def main():
    print("=" * 60)
    print("🚀 咪咕直播源抓取（Win7 32位终极修复版）")
    print("⚠️  单线程+3秒间隔，避免咪咕风控拦截")
    print("=" * 60)

    # 初始化输出文件
    with open(m3u_path, 'w', encoding='utf-8') as f:
        f.write(M3U_HEADER)
    with open(txt_path, 'w', encoding='utf-8') as f:
        f.write("")

    # 逐分类抓取（单线程）
    for live in lives:
        print(f"\n📌 开始抓取分类: {live}")
        category_id = LIVE.get(live)
        if not category_id:
            print(f"❌ 分类 {live} 无对应ID，跳过")
            continue

        # 获取频道列表
        channel_list = get_live_channel_list(category_id)
        if not channel_list:
            print(f"❌ 分类 {live} 无频道数据，跳过")
            continue
        print(f"📥 分类 {live} 共 {len(channel_list)} 个频道待抓取")

        # 逐频道处理（单线程）
        for channel_data in channel_list:
            process_channel(channel_data)

    # 按分类排序写入文件
    category_channels = defaultdict(list)
    for ch_name, (m3u_item, txt_item, category, sort_key) in channels_dict.items():
        category_channels[category].append((sort_key, ch_name, m3u_item, txt_item))

    # 分类顺序
    category_order = ['📺央视频道', '📡卫视频道', '🐼熊猫频道', '🎬影音娱乐', '📰生活资讯']

    # 写入M3U
    with open(m3u_path, 'a', encoding='utf-8') as f:
        for category in category_order:
            if category in category_channels:
                category_channels[category].sort(key=lambda x: x[0])
                for _, _, m3u_item, _ in category_channels[category]:
                    f.write(m3u_item)

    # 写入TXT
    with open(txt_path, 'a', encoding='utf-8') as f:
        for category in category_order:
            if category in category_channels and category_channels[category]:
                f.write(f"{category},#genre#\n")
                for _, _, _, txt_item in category_channels[category]:
                    f.write(txt_item)

    # 统计输出
    total_channels = len(channels_dict)
    print("\n" + "=" * 60)
    print(f"✅ 抓取任务完成！")
    print(f"📊 有效频道数: {total_channels} 个")
    print(f"📁 M3U文件路径: {os.path.abspath(m3u_path)}")
    print(f"📁 TXT文件路径: {os.path.abspath(txt_path)}")

    # 分类统计
    print("\n📋 分类统计详情：")
    for category in category_order:
        count = len(category_channels.get(category, []))
        percentage = (count / total_channels * 100) if total_channels > 0 else 0
        print(f"  {category}: {count} 个 ({percentage:.1f}%)")

    # 温馨提示
    if total_channels == 0:
        print("\n⚠️  未抓取到任何频道，可能原因：")
        print("  1. 你的IP被咪咕风控拦截（更换网络/等待1小时后重试）")
        print("  2. 咪咕接口临时调整（可联系我更新接口）")
        print("  3. Win7网络环境过旧（尝试升级Python/requests版本）")


if __name__ == "__main__":
    main()
