import requests
import time
import re
import unicodedata
from collections import defaultdict
from urllib.parse import urlparse

# ====================== 【核心配置区 所有调整均带备注】 ======================
# 目标M3U地址（已备注：自动兼容GitHub blob/raw链接，避免解析失败）
M3U_URL = "https://gh-proxy.com/https://github.com/GSD-3726/TY/blob/main/iptv_channels.m3u"

# ---------------------- 【按您要求调整：分类置顶顺序 严格固定】 ----------------------
# 备注：严格按照您要求的 央视>卫视>电影>轮播>其他 顺序置顶，写入文件时强制按此顺序输出
CATEGORY_ORDER = ["央视", "卫视", "电影", "轮播", "其他"]

# ---------------------- 【测速精准度优化配置 全参数带备注】 ----------------------
# 备注：连接超时+读取超时分离，避免网络波动误判，适配TV播放的网络要求
CONNECT_TIMEOUT = 5  # 连接超时时间（秒），仅用于TCP握手，超时直接判定无效
READ_TIMEOUT = 20    # 数据读取超时时间（秒），直播流无数据超过此时长判定无效
# 备注：测速数据块大小，从原100KB提升至512KB，样本量更大，速度计算更精准，避免瞬时波动
TEST_CHUNK_SIZE = 1024 * 512
# 备注：测速重试机制，单次失败自动重试1次，排除偶发网络抖动导致的误杀
TEST_RETRY_TIMES = 1
# 备注：TV播放最低速度阈值（KB/s），低于此值直接过滤，保证播放不卡顿，可根据带宽调整
MIN_PLAY_SPEED = 500
# 输出文件名
OUTPUT_FILE = "tv_optimized_channels.m3u"
# 请求头（适配直播源防盗链，提升请求成功率）
GLOBAL_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Accept": "*/*",
    "Connection": "keep-alive"
}
# ==============================================================================

# ---------------------- 【按您要求调整：删除全部熊猫频道相关函数与逻辑】 ----------------------
def extract_cctv_number(channel_name):
    """提取CCTV频道序号，用于央视内部精准排序，带备注"""
    match = re.search(r'CCTV[-\s]?(\d+)', channel_name, re.IGNORECASE)
    if match:
        try:
            return int(match.group(1))
        except:
            return 999
    # CGTN系列排序后置
    if 'CCTV' in channel_name or 'CGTN' in channel_name:
        cgtn_map = {'法语': 1001, '西班牙语': 1002, '俄语': 1003, '阿拉伯语': 1004, '纪录': 1005}
        for k, v in cgtn_map.items():
            if k in channel_name:
                return v
        return 1000
    return 9999

def extract_satellite_first_char(channel_name):
    """提取卫视频道首字，用于卫视拼音排序，带备注"""
    if not channel_name:
        return 'z'
    # 归一化全角/半角字符，保证排序准确
    return unicodedata.normalize('NFKC', channel_name[0]).lower()

def get_sort_key(channel_item):
    """
    【按您要求调整：排序权重重构，置顶指定分类】
    排序优先级：央视(0) > 卫视(1) > 电影(2) > 轮播(3) > 其他(4)
    分类内部排序：央视按频道号升序，卫视按首字母升序，其余分类按速度降序
    """
    channel_name = channel_item['name']
    category = channel_item['category']
    # 分类权重映射，严格对应置顶顺序
    category_weight = {
        "央视": 0,
        "卫视": 1,
        "电影": 2,
        "轮播": 3,
        "其他": 4
    }
    # 基础权重：先按分类置顶
    base_weight = category_weight.get(category, 99)
    
    # 分类内部精细化排序
    if category == "央视":
        return (base_weight, extract_cctv_number(channel_name), channel_name)
    elif category == "卫视":
        return (base_weight, extract_satellite_first_char(channel_name), channel_name)
    else:
        # 电影/轮播/其他 内部按速度从高到低排序，保证最优源在最前
        return (base_weight, -channel_item['speed'], channel_name)

# ---------------------- 【按您要求调整：分类重构，删除熊猫频道，新增指定分类】 ----------------------
def smart_classify(channel_name):
    """
    【核心调整】频道智能分类，严格匹配您要求的5个分类
    匹配优先级：央视 > 卫视 > 电影 > 轮播 > 其他，避免关键词冲突
    """
    name_clean = channel_name.strip().upper()
    # 1. 央视分类匹配（优先级最高）
    if 'CCTV' in name_clean or 'CGTN' in name_clean or '中国教育' in name_clean:
        return "央视"
    # 2. 卫视分类匹配
    if '卫视' in name_clean:
        return "卫视"
    # 3. 电影分类匹配（新增精准关键词）
    movie_keywords = ['电影', '影院', '影视', '大片', '院线', '影城']
    for keyword in movie_keywords:
        if keyword in channel_name:
            return "电影"
    # 4. 轮播分类匹配（新增精准关键词）
    loop_keywords = ['轮播', '循环', '24小时', '全天', '不间断', '全天候']
    for keyword in loop_keywords:
        if keyword in channel_name:
            return "轮播"
    # 5. 兜底分类
    return "其他"

# ---------------------- 【核心优化：测速函数重构，精准度大幅提升，全逻辑带备注】 ----------------------
def test_stream_speed(stream_url):
    """
    【精准测速优化】直播流速度测试，排除干扰因素，保证测速结果贴合实际播放体验
    优化点：
    1. 分离连接/读取超时，避免握手超时误判
    2. 重试机制，排除偶发网络抖动
    3. 排除TCP握手/DNS解析时间，仅计算纯数据下载速度
    4. 大样本数据块，避免瞬时带宽波动
    5. 自动过滤低于最低阈值的无效源
    返回值：测速成功返回速度(KB/s)，失败/不达标返回None
    """
    # 重试机制
    for retry in range(TEST_RETRY_TIMES + 1):
        try:
            # 备注：stream模式，不自动下载全量数据，仅读取指定块大小
            with requests.get(
                stream_url,
                headers=GLOBAL_HEADERS,
                timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
                stream=True,
                verify=False
            ) as resp:
                resp.raise_for_status()  # 备注：4xx/5xx状态码直接抛出异常，判定无效
                
                # 备注：排除握手时间，从数据读取开始计时，保证速度计算精准
                start_time = time.time()
                # 读取指定大小的数据块
                data = resp.raw.read(TEST_CHUNK_SIZE)
                end_time = time.time()
                
                # 备注：无数据返回，判定无效
                if not data or len(data) < 1024:
                    if retry < TEST_RETRY_TIMES:
                        time.sleep(0.5)  # 重试前短暂等待
                        continue
                    return None
                
                # 计算纯下载速度（字节/秒 转 KB/s）
                cost_time = end_time - start_time
                speed_kb_s = (len(data) / cost_time) / 1024
                
                # 备注：低于最低播放阈值，直接过滤
                if speed_kb_s < MIN_PLAY_SPEED:
                    return None
                
                return round(speed_kb_s, 2)
        
        except Exception:
            # 重试逻辑
            if retry < TEST_RETRY_TIMES:
                time.sleep(0.5)
                continue
            return None
    return None

# ---------------------- 【优化：M3U获取函数，自动处理GitHub链接，带备注】 ----------------------
def fetch_m3u_content(url):
    """获取M3U文件原始内容，自动兼容GitHub blob/raw链接，避免HTML页面解析失败"""
    print(f"[1/5] 正在获取直播源列表...")
    try:
        # 备注：自动将GitHub blob页面链接转为raw原始文本链接，解决解析失败问题
        parsed_url = urlparse(url)
        if "github.com" in parsed_url.path and "/blob/" in parsed_url.path:
            raw_url = url.replace("/blob/", "/raw/")
            print(f"    自动转换为GitHub Raw链接: {raw_url}")
            url = raw_url
        
        # 发起请求
        resp = requests.get(url, headers=GLOBAL_HEADERS, timeout=30, verify=False)
        resp.raise_for_status()
        content = resp.text
        
        # 备注：校验是否为有效M3U文件，避免返回HTML页面
        if not content.startswith("#EXTM3U"):
            print(f"    ⚠️  警告：获取的内容不是标准M3U格式，可能解析异常")
        return content
    
    except Exception as e:
        print(f"    ❌ 源列表获取失败: {str(e)}")
        return None

# ---------------------- 【优化：M3U解析函数，兼容性提升，带备注】 ----------------------
def parse_m3u(m3u_text):
    """解析M3U文本，提取频道名称和直播链接，兼容多种M3U格式"""
    print(f"[2/5] 正在解析频道列表...")
    channels = []
    if not m3u_text:
        return channels
    
    lines = m3u_text.splitlines()
    current_channel_name = "未知频道"
    current_group = None
    
    for line in lines:
        line = line.strip()
        # 跳过空行和注释行
        if not line or line.startswith("#EXTM3U"):
            continue
        
        # 解析#EXTINF行，提取频道名称
        if line.startswith("#EXTINF"):
            # 兼容带group-title的格式
            group_match = re.search(r'group-title="([^"]+)"', line, re.IGNORECASE)
            if group_match:
                current_group = group_match.group(1)
            # 提取频道名称（逗号后的内容为频道名）
            name_match = re.search(r',\s*(.+)$', line)
            if name_match:
                current_channel_name = name_match.group(1).strip()
            continue
        
        # 解析直播链接行（非#开头的行均为链接）
        if not line.startswith("#") and line.startswith(("http://", "https://", "rtmp://", "rtsp://")):
            channels.append({
                "name": current_channel_name,
                "raw_url": line,
                "raw_group": current_group
            })
    
    print(f"    ✅ 共解析到 {len(channels)} 个频道")
    return channels

# ---------------------- 【核心流程：测速筛选+排序】 ----------------------
def filter_and_sort_channels(channels):
    """批量测速筛选有效频道，并按指定规则排序"""
    print(f"[3/5] 开始频道测速筛选（最低播放速度要求：{MIN_PLAY_SPEED} KB/s）...")
    valid_channels = []
    total_count = len(channels)
    
    for index, channel in enumerate(channels):
        channel_name = channel['name']
        channel_url = channel['raw_url']
        # 进度输出
        print(f"    测速 [{index+1}/{total_count}] {channel_name:<30}", end="", flush=True)
        
        # 执行测速
        speed = test_stream_speed(channel_url)
        if speed:
            # 分类匹配
            category = smart_classify(channel_name)
            valid_channels.append({
                "name": channel_name,
                "url": channel_url,
                "speed": speed,
                "category": category
            })
            print(f"✅ 有效 速度: {speed} KB/s 分类: {category}")
        else:
            print(f"❌ 无效/速度不达标")
    
    # 按指定规则排序
    print(f"[4/5] 正在按置顶规则排序频道...")
    valid_channels.sort(key=get_sort_key)
    print(f"    ✅ 筛选完成，有效频道数：{len(valid_channels)} 个")
    return valid_channels

# ---------------------- 【优化：M3U文件写入，严格按分类置顶顺序】 ----------------------
def write_optimized_m3u(channels, output_path):
    """写入最终优化后的M3U文件，严格按指定分类顺序输出，适配TV播放器"""
    print(f"[5/5] 正在写入优化后的M3U文件...")
    
    # 按分类分组
    category_channel_map = defaultdict(list)
    for channel in channels:
        category_channel_map[channel['category']].append(channel)
    
    # 写入文件
    with open(output_path, 'w', encoding='utf-8') as f:
        # M3U文件头
        f.write("#EXTM3U\n")
        # 严格按指定的置顶顺序写入分类
        for category in CATEGORY_ORDER:
            if category not in category_channel_map:
                continue
            channel_list = category_channel_map[category]
            print(f"    写入分类 [{category}]：{len(channel_list)} 个频道")
            # 写入该分类下的所有频道
            for channel in channel_list:
                # 写入EXTINF行，带分类group-title，适配TV播放器的文件夹分类
                f.write(f'#EXTINF:-1 group-title="{category}",{channel["name"]}\n')
                # 写入直播链接
                f.write(f'{channel["url"]}\n')
    
    print(f"\n🎉 全部任务执行完成！")
    print(f"📁 优化后的M3U文件路径：{output_path}")
    print(f"📊 最终有效频道总数：{len(channels)} 个")
    # 分类统计输出
    for category in CATEGORY_ORDER:
        count = len(category_channel_map.get(category, []))
        print(f"    【{category}】：{count} 个频道")

# ---------------------- 主程序入口 ----------------------
if __name__ == "__main__":
    # 关闭SSL警告，避免部分自签名证书站点请求失败
    requests.packages.urllib3.disable_warnings()
    
    # 主流程执行
    m3u_raw_content = fetch_m3u_content(M3U_URL)
    if m3u_raw_content:
        raw_channel_list = parse_m3u(m3u_raw_content)
        if raw_channel_list:
            optimized_channel_list = filter_and_sort_channels(raw_channel_list)
            if optimized_channel_list:
                write_optimized_m3u(optimized_channel_list, OUTPUT_FILE)
            else:
                print("❌ 未筛选出符合速度要求的有效频道，请检查源地址或降低最低速度阈值")
        else:
            print("❌ 未能解析出任何频道，请检查M3U地址是否有效")
    else:
        print("❌ 无法获取M3U源文件，请检查网络连接或地址是否正确")
