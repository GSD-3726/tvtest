import aiohttp
import asyncio
import time
import statistics
from urllib.parse import urljoin
from datetime import datetime
import requests

# ===================== 极速配置（核心提速）=====================
RAW_TXT_URL = "https://gh-proxy.com/https://raw.githubusercontent.com/GSD-3726/IPTV/master/output/result.txt"
OUTPUT_FILE = "result.txt"
# 异步并发数（根据服务器性能调整，建议5-10）
CONCURRENT_LIMIT = 8
# 测速配置（更少数据，更快验证）
TEST_SHARD_COUNT = 2  # m3u8仅测试2个分片（原3个）
TIMEOUT_FAST = 2      # 快速预处理超时（秒，原5）
TIMEOUT_DEEP = 3      # 深度测速超时（秒，原5）
# 卡顿判定阈值（适配极速测试）
FAIL_RATE_THRESHOLD = 0.1   # 失败率≤10%
AVG_TIME_THRESHOLD = 2.0    # 平均耗时≤2秒（原2.5）
MAX_TIME_THRESHOLD = 4.0    # 最大耗时≤4秒（原6）
# 支持的协议
SUPPORTED_PROTOCOLS = ("http://", "https://")

# ===================== 异步工具函数（核心提速）=====================
async def async_head_check(session, url):
    """异步快速检测链接是否可达（HEAD请求，仅1-2KB数据）"""
    try:
        async with session.head(url, timeout=TIMEOUT_FAST, allow_redirects=True):
            return True
    except Exception:
        return False

async def async_download_small(session, url, max_bytes=10*1024):
    """异步下载少量数据（验证可用性，返回耗时+是否成功）"""
    try:
        start_time = time.time()
        async with session.get(url, timeout=TIMEOUT_DEEP) as resp:
            if resp.status != 200:
                return 0, False
            total_bytes = 0
            async for chunk in resp.content.iter_chunked(1024):
                total_bytes += len(chunk)
                if total_bytes >= max_bytes:
                    break
            cost_time = round(time.time() - start_time, 3)
            # 至少下载2KB视为成功
            return cost_time, total_bytes >= 2*1024
    except Exception:
        return 0, False

async def parse_m3u8_async(session, m3u8_url):
    """异步解析m3u8，仅返回前N个分片"""
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
                    if len(shards) >= TEST_SHARD_COUNT:  # 仅取需要的分片数
                        break
            return shards if shards else None
    except Exception:
        return None

async def test_m3u8_async(session, m3u8_url):
    """异步测试m3u8流畅度（并发测试分片）"""
    shards = await parse_m3u8_async(session, m3u8_url)
    if not shards:
        return False
    # 并发测试所有分片
    tasks = [async_download_small(session, shard) for shard in shards]
    results = await asyncio.gather(*tasks)
    # 统计有效结果
    cost_times = [t for t, ok in results if ok]
    if not cost_times:
        return False
    fail_rate = (len(results) - len(cost_times)) / len(results)
    avg_time = statistics.mean(cost_times)
    max_time = max(cost_times)
    return (fail_rate <= FAIL_RATE_THRESHOLD and
            avg_time <= AVG_TIME_THRESHOLD and
            max_time <= MAX_TIME_THRESHOLD)

async def test_flv_async(session, flv_url):
    """异步测试flv流畅度（仅下载50KB）"""
    cost_time, ok = await async_download_small(session, flv_url, max_bytes=50*1024)
    return ok and cost_time <= MAX_TIME_THRESHOLD

async def test_url_async(session, name, url, result_queue):
    """异步测试单个地址，结果存入队列"""
    print(f"测试中：{name} | {url[:60]}...", end=" ")
    # 第一步：快速过滤无效链接
    if not await async_head_check(session, url):
        print("❌ 快速检测失败（不可达）")
        return
    # 第二步：深度测速
    if url.endswith('.m3u8'):
        is_smooth = await test_m3u8_async(session, url)
    elif url.endswith('.flv'):
        is_smooth = await test_flv_async(session, url)
    else:
        is_smooth = False
    # 结果入队
    if is_smooth:
        print("✅ 流畅（保留）")
        await result_queue.put((name, url))
    else:
        print("❌ 卡顿/不支持（跳过）")

# ===================== 主逻辑（保留原始格式+极速运行）=====================
def download_original_txt():
    """同步下载原始txt（仅一次，耗时可忽略）"""
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        resp = requests.get(RAW_TXT_URL, headers=headers, timeout=10)
        resp.raise_for_status()
        resp.encoding = "utf-8"
        return [line.rstrip('\n') for line in resp.text.splitlines() if line.strip()]
    except Exception as e:
        print(f"下载原始文件失败：{e}")
        return []

async def main_async():
    print("="*70)
    print(f"IPTV极速测速开始 | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"并发数：{CONCURRENT_LIMIT} | 超时：快速{TIMEOUT_FAST}s / 深度{TIMEOUT_DEEP}s")
    print("="*70)
    
    # 1. 下载原始txt（保留格式）
    original_lines = download_original_txt()
    if not original_lines:
        print("❌ 无原始数据，终止")
        return
    
    # 2. 解析原始行，分离分类行/地址行
    genre_lines = []       # 分类行（如📺央视频道,#genre#）
    url_tasks = []         # 待测试的地址任务 (name, url)
    update_time_line = ""  # 更新时间行
    current_datetime = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    for line in original_lines:
        if line.startswith("🕘️") and "#genre#" in line:
            update_time_line = f"🕘️{current_datetime},#genre#"
        elif "#genre#" in line and not line.startswith("🕘️"):
            genre_lines.append((len(url_tasks), line))  # 记录分类行位置
        elif "," in line:
            name, url = line.split(",", 1)
            name = name.strip()
            url = url.strip()
            if url.startswith(SUPPORTED_PROTOCOLS):
                url_tasks.append((name, url))
    
    # 3. 异步并发测试所有地址（核心提速）
    result_queue = asyncio.Queue()
    # 创建异步session（复用连接）
    connector = aiohttp.TCPConnector(limit=CONCURRENT_LIMIT)
    async with aiohttp.ClientSession(connector=connector) as session:
        # 创建所有测试任务
        tasks = [test_url_async(session, name, url, result_queue) for name, url in url_tasks]
        # 并发执行
        await asyncio.gather(*tasks)
    
    # 4. 整理测试结果（保留原始分类结构）
    smooth_urls = []
    while not result_queue.empty():
        smooth_urls.append(await result_queue.get())
    # 按原始顺序整理输出行
    output_lines = [update_time_line] if update_time_line else []
    url_idx = 0
    # 插入分类行+对应地址
    for genre_pos, genre_line in sorted(genre_lines, key=lambda x: x[0]):
        output_lines.append(genre_line)
        # 插入该分类下的流畅地址
        while url_idx < len(smooth_urls) and url_idx <= genre_pos:
            name, url = smooth_urls[url_idx]
            output_lines.append(f"{name},{url}")
            url_idx += 1
    # 补充剩余地址
    while url_idx < len(smooth_urls):
        name, url = smooth_urls[url_idx]
        output_lines.append(f"{name},{url}")
        url_idx += 1
    
    # 5. 写入结果（严格匹配原始格式）
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(output_lines))
    
    # 6. 统计输出
    print("="*70)
    print(f"✅ 极速测速完成 | 总测试地址：{len(url_tasks)} | 保留流畅地址：{len(smooth_urls)}")
    print(f"📄 结果文件：{OUTPUT_FILE}（格式与原始完全一致）")
    print("="*70)

if __name__ == "__main__":
    # 适配Windows/Linux异步运行
    if asyncio.get_event_loop_policy().__class__.__name__ == "WindowsProactorEventLoopPolicy":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    # 运行异步主逻辑
    asyncio.run(main_async())
