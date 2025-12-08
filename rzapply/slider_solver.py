# slider_solver.py (同步专用版)
import base64
import random
import time  # 替换 asyncio
import httpx
from playwright.sync_api import Page, Locator # 替换 async_api

class JfbymSliderSolver:
    """
    云码打码平台-滑块验证码通用解决方案 (同步版)
    """
    def __init__(self, api_token: str, api_type: str = "22222"):
        self.api_url = "http://api.jfbym.com/api/YmServer/customApi"
        self.api_token = api_token
        self.api_type = api_type

    def run(self, page: Page, bg_selector: str, btn_selector: str, offset: int = 0, scale: float = 1.0):
        """
        执行完整的滑块破解流程 (同步)
        """
        print(f"[SliderSolver] 开始识别滑块: Offset={offset}, Scale={scale}")
        
        # 1. 定位元素
        bg_locator = page.locator(bg_selector).first
        btn_locator = page.locator(btn_selector).first

        # 确保元素可见 (Sync API 不需要 await)
        try:
            bg_locator.wait_for(state="visible", timeout=5000)
            btn_locator.wait_for(state="visible", timeout=5000)
        except Exception:
            raise RuntimeError("[SliderSolver] 无法定位到滑块或背景图，请检查选择器")

        # 2. 截图
        time.sleep(0.5) # 替换 asyncio.sleep
        img_bytes = bg_locator.screenshot()
        
        # 3. 调用 API 获取距离
        api_distance = self._call_api(img_bytes)
        print(f"[SliderSolver] API返回距离: {api_distance}")

        # 4. 计算实际拖拽距离
        final_distance = (api_distance * scale) + offset
        print(f"[SliderSolver] 最终计划拖拽距离: {final_distance}")

        # 5. 执行拟人化拖拽
        self._drag_with_track(page, btn_locator, final_distance)
        print("[SliderSolver] 滑动动作完成")

    def _call_api(self, image_bytes: bytes) -> int:
        """内部方法：调用打码平台 API (同步 HTTP)"""
        img_b64 = base64.b64encode(image_bytes).decode("ascii")
        payload = {
            "token": self.api_token,
            "type": self.api_type,
            "image": img_b64,
        }

        # 使用同步的 Client
        with httpx.Client(timeout=20) as client:
            resp = client.post(self.api_url, json=payload)
            data = resp.json()

        if data.get("code") != 10000:
            raise RuntimeError(f"[SliderSolver] API报错: {data}")

        res_data = data.get("data")
        val = None
        if isinstance(res_data, dict):
            val = res_data.get("data")
        elif isinstance(res_data, list) and res_data:
            val = res_data[0].get("data")
        else:
            val = res_data

        if val is None:
            raise RuntimeError(f"[SliderSolver] 无法解析返回数据: {data}")
            
        return int(float(val))

    def _get_track(self, distance: float):
        """内部方法：生成物理拟人轨迹"""
        track = []
        current = 0
        mid = distance * 4 / 5
        t = 0.2
        v = 0
        
        while current < distance:
            if current < mid:
                a = 3
            else:
                a = -4
            
            v0 = v
            v = v0 + a * t
            move = v0 * t + 0.5 * a * t * t
            if move < 1: move = 1
            current += move
            
            y_jitter = random.choice([-1, 0, 1]) if random.random() > 0.6 else 0
            track.append((round(move), y_jitter, random.uniform(0.01, 0.02)))

        overshoot = random.randint(3, 6)
        for _ in range(overshoot):
            track.append((1, 0, random.uniform(0.01, 0.03)))
        for _ in range(overshoot):
            track.append((-1, 0, random.uniform(0.03, 0.05)))
            
        return track

    def _drag_with_track(self, page: Page, slider: Locator, distance: float):
        """内部方法：执行拖拽 (同步)"""
        box = slider.bounding_box() # 不需要 await
        if not box:
            raise RuntimeError("无法获取滑块坐标")
            
        start_x = box["x"] + box["width"] / 2
        start_y = box["y"] + box["height"] / 2

        track = self._get_track(distance)

        page.mouse.move(start_x, start_y)
        page.mouse.down()
        
        current_x, current_y = start_x, start_y
        for x_move, y_move, t_sleep in track:
            current_x += x_move
            current_y += y_move
            page.mouse.move(current_x, current_y, steps=1)
            time.sleep(t_sleep) # 替换 asyncio.sleep
        
        time.sleep(random.uniform(0.3, 0.6))
        page.mouse.up()