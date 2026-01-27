import aiohttp
import asyncio
import time
import statistics
from urllib.parse import urljoin
from datetime import datetime
import requests

# ===================== 配置（保留1080P阈值+极速并发）=====================
RAW_TXT_URL = "https://gh-proxy.com/https://raw.githubusercontent.com/GSD-3726/IPTV/master/output/result.txt"
OUTPUT_FILE = "result.txt"
CONCURRENT_LIMIT = 8  # 并发数（5-10最佳，避免被限）
TEST_SHARD_COUNT = 2  # m3u8分片测试数
TIMEOUT_LIGHT = 2     # 轻量检测超时（秒）
TIMEOUT_DEEP = 3      # 深度测速超时（秒）
# 1080P流畅阈值
FAIL_RATE_THRESHOLD = 0.05
AVG_TIME_THRESHOLD = 1.5
MAX_TIME_THRESHOLD = 4.0
MIN_HD_SHARD_SIZE = 102400  # 1080P分片≥100KB
SUPPORTED_PROTOCOLS = ("http://", "https://")

# ===================== 异步工具函数（修复检测逻辑，移除HEAD）=====================
async def async_light_check(session, url):
    """替代HEAD的轻量GET检测：仅下载1KB数据，兼容所有IPTV源"""
    try:
        async with session.get(url, timeout=TIMEOUT_LIGHT) as resp:
            # 仅读取1KB数据，不下载完整内容
            await resp.content.read(1024)
            return resp.status == 200
    except Exception:
        return False

async def async_download_hd(session, url, max_bytes):
    """异步下载指定大小数据，返回（耗时，是否成功，下载字节数）"""
    try:
        start_time = time.time()
        async with session.get(url, timeout=TIMEOUT_DEEP) as resp:
            if resp.status != 200:
                return 0, False, 0
            total_bytes = 0
            async for chunk in resp.content.iter_chunked(1024):
                total_bytes += len(chunk)
                if total_bytes >= max_bytes:
                    break
            cost_time = round(time.time() - start_time, 3)
            return cost_time, total_bytes >= 2*1024, total_bytes  # 至少2KB视为有效
    except Exception:
        return 0, False, 0

async def parse_m3u8_async(session, m3u8_url):
    """异步解析m3u8，仅取前N个分片"""
    try:
        async with session.get(m3u8_url, timeout=TIMEOUT_DEEP) as resp:
            if resp.status != 200:
                return None
            text = await resp.text()
            base_url = m3u8_url.rsplit('/', 1)[0] + '/' if '/' in m3u8_url else ''
            shards = []
            for line in text.splitlines():
                line = line.strip()
                if line and not line.startswith('#') and line.endswith('.ts'):
                    shards.append(urljoin(base_url, line))
                    if len(shards) >= TEST_SHARD_COUNT:
                        break
            return shards if shards else None
    except Exception:
        return None

async def test_m3u8_async(session, m3u8_url):
    """异步测试1080P m3u8流：分片大小+速度双重验证"""
    shards = await parse_m3u8_async(session, m3u8_url)
    if not shards:
        return False
    # 并发测试分片
    tasks = [async_download_hd(session, shard, MIN_HD_SHARD_SIZE) for shard in shards]
    results = await asyncio.gather(*tasks)
    # 统计有效结果（同时满足：下载成功+分片≥100KB）
    cost_times = []
    for t, ok, b in results:
        if ok and b >= MIN_HD_SHARD_SIZE:
            cost_times.append(t)
    if not cost_times:
        return False
    # 1080P阈值判定
    fail_rate = (len(results) - len(cost_times)) / len(results)
    avg_time = statistics.mean(cost_times)
    max_time = max(cost_times)
    return (fail_rate <= FAIL_RATE_THRESHOLD and
            avg_time <= AVG_TIME_THRESHOLD and
            max_time <= MAX_TIME_THRESHOLD)

async def test_flv_async(session, flv_url):
    """异步测试1080P flv流：下载200KB验证大小+速度"""
    cost_time, ok, total_bytes = await async_download_hd(session, flv_url, 204800)
    # 1080P FLV要求：下载成功+≥200KB+耗时≤阈值
    return ok and total_bytes >= 204800 and cost_time <= MAX_TIME_THRESHOLD

async def test_url_async(session, name, url, result_queue):
    """异步测试单个地址：轻量GET检测→深度测速"""
    print(f"测试中：{name} | {url[:60]}...", end=" ")
    # 第一步：轻量GET检测（替代HEAD，兼容所有服务器）
    if not await async_light_check(session, url):
        print("❌ 链接不可达/无效")
        return
    # 第二步：深度测速（1080P标准）
    if url.endswith('.m3u8'):
        is_smooth = await test_m3u8_async(session, url)
    elif url.endswith('.flv'):
        is_smooth = await test_flv_async(session, url)
    else:
        print("❌ 非m3u8/flv协议")
        return
    # 结果入队
    if is_smooth:
        print("✅ 1080P流畅（保留）")
        await result_queue.put((name, url))
    else:
        print("❌ 1080P卡顿/低清")

# ===================== 主逻辑（保留原始格式+极速运行）=====================
def download_original_txt():
    """同步下载原始txt（仅一次，耗时可忽略）"""
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
        resp = requests.get(RAW_TXT_URL, headers=headers, timeout=10)
        resp.raise_for_status()
        resp.encoding = "utf-8"
        return [line.rstrip('\n') for line in resp.text.splitlines() if line.strip()]
    except Exception as e:
        print(f"❌ 下载原始文件失败：{e}")
        return []

async def main_async():
    print("="*70)
    print(f"IPTV极速测速（1080P）| {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"并发数：{CONCURRENT_LIMIT} | 轻量检测{TIMEOUT_LIGHT}s / 深度测速{TIMEOUT_DEEP}s")
    print("="*70)
    
    # 1. 下载原始txt（保留所有格式）
    original_lines = download_original_txt()
    if not original_lines:
        print("❌ 无原始数据，终止流程")
        return
    
    # 2. 解析原始行：分离分类行/地址行/更新时间行
    genre_map = {}        # 分类行位置映射 {地址索引: 分类行}
    url_tasks = []        # 待测试地址 [(name, url)]
    update_time_line = "" # 更新时间行
    current_datetime = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    for line in original_lines:
        if line.startswith("🕘️") and "#genre#" in line:
            # 处理更新时间行，保留原始格式
            update_time_line = f"🕘️{current_datetime},#genre#"
        elif "#genre#" in line and not line.startswith("🕘️"):
            # 记录分类行，关联后续地址
            genre_map[len(url_tasks)] = line
        elif "," in line:
            # 解析地址行，仅分割第一个逗号（兼容链接含逗号）
            name_part, url_part = line.split(",", 1)
            name = name_part.strip()
            url = url_part.strip()
            if url.startswith(SUPPORTED_PROTOCOLS):
                url_tasks.append((name, url))

    # 3. 异步并发测试所有地址（核心提速）
    result_queue = asyncio.Queue()
    # 创建异步Session：复用连接+设置UA（模拟浏览器，防屏蔽）
    connector = aiohttp.TCPConnector(limit=CONCURRENT_LIMIT)
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
    async with aiohttp.ClientSession(connector=connector, headers=headers) as session:
        tasks = [test_url_async(session, n, u, result_queue) for n, u in url_tasks]
        await asyncio.gather(*tasks)

    # 4. 整理结果：严格保留原始分类结构和格式
    smooth_urls = []
    while not result_queue.empty():
        smooth_urls.append(await result_queue.get())
    smooth_urls = sorted(smooth_urls, key=lambda x: url_tasks.index((x[0], x[1])))  # 保留原始顺序

    output_lines = [update_time_line] if update_time_line else []
    current_url_idx = 0
    # 按原始顺序插入分类行和对应地址
    sorted_genre = sorted(genre_map.items(), key=lambda x: x[0])
    for idx, genre_line in sorted_genre:
        output_lines.append(genre_line)
        # 插入该分类下的流畅地址
        while current_url_idx < len(smooth_urls) and current_url_idx < len(url_tasks) and url_tasks.index(smooth_urls[current_url_idx]) >= idx:
            if current_url_idx < len(smooth_urls):
                n, u = smooth_urls[current_url_idx]
                output_lines.append(f"{n},{u}")
                current_url_idx += 1
            else:
                break
    # 补充剩余无分类的流畅地址
    while current_url_idx < len(smooth_urls):
        n, u = smooth_urls[current_url_idx]
        output_lines.append(f"{n},{u}")
        current_url_idx += 1

    # 5. 写入结果：严格匹配原始txt格式（无任何多余字符）
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(output_lines))

    # 6. 统计输出
    print("="*70)
    print(f"✅ 1080P测速完成 | 总测试地址：{len(url_tasks)} | 保留流畅地址：{len(smooth_urls)}")
    print(f"📄 结果文件：仓库根目录/{OUTPUT_FILE}（格式与原始完全一致）")
    print("="*70)

if __name__ == "__main__":
    # 适配Windows/Linux异步运行（解决GitHub Actions/Ubuntu兼容问题）
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    except:
        pass
    asyncio.run(main_async())
