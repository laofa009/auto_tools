from __future__ import annotations
import json
import re
from pathlib import Path
from playwright.sync_api import Locator, Page
from PIL import Image   # 需要: pip install pillow
import os
import re
import sys
from pathlib import Path
from typing import Callable, Sequence

from playwright.sync_api import Playwright, TimeoutError, sync_playwright

from models import DEFAULT_OWNER_TYPE, OWNER_TYPE_ID_OPTIONS, Task

AUTH_DIR = Path("playwright/.auth")
STORAGE_STATE = AUTH_DIR / "storage_state.json"
STORAGE_META = AUTH_DIR / "storage_meta.json"

MS_PLAYWRIGHT = (
    Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
    / "playwright"
    / "driver"
    / "package"
    / "ms-playwright"
)
if not MS_PLAYWRIGHT.exists():
    MS_PLAYWRIGHT = Path.home() / "AppData/Local/ms-playwright"

os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(MS_PLAYWRIGHT))

DEFAULT_CERT_CONTACT_NAME = "朱砂"
DEFAULT_CERT_CONTACT_UNIT = "中国江苏徐州邳州市东湖街道汉府西侧环球新天地二栋"
DEFAULT_CERT_DETAIL_ADDRESS = DEFAULT_CERT_CONTACT_UNIT
DEFAULT_CERT_POSTAL_CODE = "221300"
DEFAULT_CERT_PHONE = "18168245213"

def ensure_storage_state_file() -> bool:
    """Ensure the storage_state file exists, returning True if it already existed."""
    AUTH_DIR.mkdir(parents=True, exist_ok=True)
    if STORAGE_STATE.exists():
        return True
    STORAGE_STATE.write_text("{}")
    return False


LogFn = Callable[[str], None] | None


class TaskUploader:
    """Uploads a single task via Playwright automation."""

    def __init__(
        self,
        headless: bool = False,
        default_username: str | None = None,
        default_password: str | None = None,
        default_login_type: str | None = None,
        default_submit_role: str | None = None,
    ):
        self.headless = headless
        self.default_username = default_username or os.environ.get("RZAPPLY_USERNAME", "Yf19942050676_")
        self.default_password = default_password or os.environ.get("RZAPPLY_PASSWORD", "Yf19942050676_")
        self.default_login_type = (default_login_type or os.environ.get("RZAPPLY_LOGIN_TYPE") or "机构").strip()
        self.default_submit_role = (default_submit_role or os.environ.get("RZAPPLY_SUBMIT_ROLE") or "申请人").strip()
        self.last_login_username: str | None = None
        self.last_login_type: str | None = None
        self._playwright: Playwright | None = None
        self._browser = None
        self._context = None

    def upload(self, task: Task, log: LogFn = None) -> dict[str, Path | None]:
        self._log(log, f"开始上传：{task.display_name()}")
        ensure_storage_state_file()
        self._load_state_meta()
        artifacts = self._run(task, log)
        self._log(log, "上传流程结束")
        return artifacts

    def _ensure_playwright(self) -> None:
        if not self._playwright:
            self._playwright = sync_playwright().start()

    def _close_context(self) -> None:
        try:
            if self._context:
                self._context.close()
        except Exception:
            pass
        try:
            if self._browser:
                self._browser.close()
        except Exception:
            pass
        self._context = None
        self._browser = None

    def _load_state_meta(self) -> None:
        text: str | None = None
        try:
            text = STORAGE_META.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            # 兼容旧版本使用系统默认编码写入的文件，例如 Windows 上的 cp936
            try:
                text = STORAGE_META.read_text()
            except Exception:
                text = None
        except FileNotFoundError:
            text = None
        except Exception:
            text = None

        if not text:
            self.last_login_username = None
            self.last_login_type = None
            return

        try:
            data = json.loads(text)
        except Exception:
            self.last_login_username = None
            self.last_login_type = None
            return

        self.last_login_username = data.get("username") or None
        self.last_login_type = data.get("login_type") or None

    def _save_state_meta(self, username: str, login_type: str) -> None:
        try:
            STORAGE_META.parent.mkdir(parents=True, exist_ok=True)
            STORAGE_META.write_text(
                json.dumps({"username": username, "login_type": login_type}, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception:
            pass

    def _get_context(self, task: Task, log: LogFn):
        username, password, login_type, submit_role = self._get_login_context(task)
        need_new = False
        if not self._context or not self._browser or not self._browser.is_connected():
            need_new = True
        else:
            if self.last_login_username and (
                self.last_login_username != username or (self.last_login_type or "") != login_type
            ):
                need_new = True
        if need_new:
            self._log(log, "启动浏览器")
            self._close_context()
            self._ensure_playwright()
            self._browser = self._playwright.chromium.launch(headless=self.headless, args=["--start-maximized"])
            self._context = self._browser.new_context(storage_state=str(STORAGE_STATE), no_viewport=True)
        page = self._context.new_page()
        return page, username, password, login_type, submit_role

    def _run(self, task: Task, log: LogFn) -> dict[str, Path | None]:
        page, username, password, login_type, submit_role = self._get_context(task, log)
        self._log(log, "打开官网")
        page.goto("https://register.ccopyright.com.cn/registration.html#/index")
        skip_button = page.get_by_text("跳过")
        if skip_button.count() > 0 and skip_button.is_visible():
            skip_button.click()
        page.locator(".soft").click()
        # with page.expect_popup() as page1_info:
        #     page.locator(".pic > a").first.click()
        # page1 = page1_info.value
        self._enter_application_page(page, log)

        if self._login_if_needed(self._context, page, username, password, login_type, log):
            self._log(log, "登录成功，保存 storage_state")
            self._context.storage_state(path=str(STORAGE_STATE))
            self.last_login_username = username
            self.last_login_type = login_type

        # 根据办理身份选择申报角色
        if submit_role == "申请人":
            page.get_by_text("我是申请人 我为自己创作的软件申请著作权登记").click()
        else:
            page.get_by_text("我是代理人 我为他人创作的软件申请著作权登记").click()
            page.get_by_role("button", name="确定").click()
        self._log(log, "填写软件申请信息")
        self._fill_basic_form(page, task)
        self._log(log, "填写软件开发信息")
        self._fill_soft_dev_info_form(page, task, login_type, submit_role)
        self._log(log, "填写软件功能与特点")
        sign_pdf_path = self._fill_soft_feature_form(page, task, login_type, submit_role, log)
        self._log(log, "页面操作完成，关闭浏览器")
        page.close()
        return {"sign_page_pdf": sign_pdf_path}

    def _get_login_context(self, task: Task) -> tuple[str, str, str, str]:
        username = (task.config.get("login_username") or "").strip()
        password = (task.config.get("login_password") or "").strip()
        login_type = (task.config.get("login_type") or "").strip() or self.default_login_type
        submit_role = (task.config.get("submit_role") or "").strip() or self.default_submit_role
        if not username:
            username = self.default_username
        if not password:
            password = self.default_password
        return username, password, login_type, submit_role

    def _login_if_needed(self, context, page, username: str, password: str, login_type: str, log: LogFn) -> bool:
        """Return True if login occurred."""
        login_required = False
        username_input = page.get_by_role("textbox", name="请输入用户名/手机号/邮箱")
        try:
            username_input.wait_for(state="visible", timeout=5000)
            login_required = True
        except TimeoutError:
            login_required = False

        force_relogin = (
            self.last_login_username is not None
            and (self.last_login_username != username or (self.last_login_type or "") != login_type)
        )

        if not login_required and force_relogin:
            self._log(log, "检测到账号或类型变更，强制清除会话重新登录")
            context.clear_cookies()
            page.evaluate("() => { localStorage.clear(); sessionStorage.clear(); }")
            page.goto("https://register.ccopyright.com.cn/registration.html#/index")
            try:
                page.get_by_text("跳过", exact=True).click(timeout=2000)
            except Exception:
                pass
            try:
                page.locator(".soft").click(timeout=5000)
            except Exception:
                pass
            self._enter_application_page(page, log)
            username_input = page.get_by_role("textbox", name="请输入用户名/手机号/邮箱")
            try:
                username_input.wait_for(state="visible", timeout=5000)
                login_required = True
            except TimeoutError:
                login_required = False

        if login_required:
            self._log(log, "检测到登录界面，执行登录流程")
            if not username or not password:
                raise ValueError("未配置版权中心登录账号或密码")
            tab_text = "机构" if login_type == "机构" else "个人用户"
            page.get_by_text(tab_text, exact=True).click()
            username_input.click()
            username_input.fill(username)
            password_input = page.get_by_role("textbox", name="请输入密码")
            password_input.click()
            password_input.fill(password)
            page.get_by_role("button", name="立即登录").click()
            self._enter_application_page(page, log)
            self._save_state_meta(username, login_type)
            return True
        self._log(log, "保持登录状态，无需重新登录")
        return False

    def _enter_application_page(self, page: Page, log: LogFn, retry: int = 2, wait_ms: int = 8000) -> None:
        """Try to click R11 entry; tolerate慢加载，避免误判没有按钮。"""
        self._log(log, "进入办理页面")
        for idx in range(retry + 1):
            try:
                button = page.get_by_role("cell", name="R11").get_by_role("button")
                button.wait_for(state="visible", timeout=wait_ms)
                button.click()
                return
            except TimeoutError:
                if idx < retry:
                    self._log(log, f"R11 入口未出现，重试 {idx + 1}/{retry}")
                    page.wait_for_timeout(1000)
                    continue
                self._log(log, "未找到 R11 入口按钮，可能已经在办理页面")
                return

    def _fill_basic_form(self, page, task: Task) -> None:
        # config = task.config
        short_name = self._resolve_short_name(task)

        page.get_by_role("textbox", name="请输入软件全称").fill(task.meta.get("software_name", ""))
        short_name_locator = page.get_by_role(
            "textbox", name="请输入软件简称，如无简称请留空，不要填写“无”。"
        )
        short_name_locator.fill(short_name)
        page.get_by_role("textbox", name="请输入版本号").fill(task.meta.get("version", "V1.0"))
        page.get_by_role("button", name="下一步").click()

    def _fill_soft_dev_info_form(self, page, task: Task, login_type: str, submit_role: str) -> None:
        if submit_role == "申请人":
            software_category = task.meta.get("software_category", "应用软件")
            if software_category not in ["应用软件", "嵌入式软件", "中间件", "操作系统"]:
                software_category = "应用软件"
            page.locator(".box > .icon").first.click()
            # 应用软件，嵌入式软件，中间件，操作系统
            # 在这里等待下拉选项中的操作系统出来，再继续，
            category_option = page.get_by_text(software_category, exact=True)
            category_option.wait_for(state="visible", timeout=5000)
            category_option.click()
            page.get_by_text("原创").click()
            page.get_by_text("单独开发", exact=True).click()
            completion_date = task.meta.get("completion_date")  # 例如 "2024-06-15"
            if completion_date:
              try:
                self._select_date_in_picker(page, completion_date)
              except Exception as e:
                # 如果日期控件结构变化，日志里能看到
                self._log("选择完成日期失败：{completion_date}，错误：{e}")

            page.get_by_text("未发表").click()
            next_button = page.get_by_role("button", name="下一步")
            next_button.wait_for(state="visible", timeout=20000)
            next_button.click()
            return

        software_category = task.meta.get("software_category", "应用软件")
        if software_category not in ["应用软件", "嵌入式软件", "中间件", "操作系统"]:
            software_category = "应用软件"
        page.locator(".box > .icon").first.click()
        # 应用软件，嵌入式软件，中间件，操作系统
        # 在这里等待下拉选项中的操作系统出来，再继续，
        category_option = page.get_by_text(software_category, exact=True)
        category_option.wait_for(state="visible", timeout=5000)
        category_option.click()
        page.get_by_text("原创").click()
        page.get_by_text("单独开发", exact=True).click()
        completion_date = task.meta.get("completion_date")
        if completion_date:
            try:
                self._select_date_in_picker(page, completion_date)
            except Exception as e:
            # 如果日期控件结构变化，日志里能看到
               self._log("选择完成日期失败：{completion_date}，错误：{e}")
        page.get_by_text("未发表").click()
        page.locator(".country_select > .hd-select > .box").click()
        page.locator(".country_select > .hd-select > .dropdown > .hd_scroll > .hd_scroll_content > div").first.click()
        page.locator(".label > .icon").click()
        owners = task.config.get("owners") or []
        primary_owner = owners[0] if owners else {}
        province = task.config.get("province") or primary_owner.get("province", "")
        city = task.config.get("city") or primary_owner.get("city", "")
        owner_type = task.config.get("name_type") or primary_owner.get("name_type") or DEFAULT_OWNER_TYPE
        owner_name = task.config.get("name") or primary_owner.get("name") or ""
        owner_id_type = task.config.get("id_type") or primary_owner.get("id_type")
        card_input = task.config.get("card_input") or primary_owner.get("card_input", "")

        if not owner_id_type:
            owner_id_type = OWNER_TYPE_ID_OPTIONS.get(owner_type, OWNER_TYPE_ID_OPTIONS[DEFAULT_OWNER_TYPE])[0]

        if not all([province, city, owner_name]):
            raise ValueError("著作权人信息不完整：请在 GUI 中填写姓名、省份和城市")

        page.get_by_text("省份").click()
        page.get_by_text(province, exact=True).click()
        page.get_by_text("城市").click()
        page.get_by_text(city, exact=True).click()
        #著作权人
        #国家
        #省份
        #自然人，企业法人，机关法人，事业单位法人，社会团体法人
        page.locator(".formGroup-item-body-left-item > .hd-select > .box > .icon").first.click()
        page.get_by_text(owner_type, exact=True).click()
        page.get_by_role("textbox", name="姓名或名称，与身份证明文件保持一致").fill(owner_name)
        page.locator("div:nth-child(4) > .hd-select > .box > .icon").click()
        page.get_by_text(owner_id_type, exact=True).click()
        page.get_by_role("textbox", name="请输入证件号码").fill(card_input)
        page.get_by_text("保存", exact=True).click()
        edit_button = page.get_by_role("button", name="编辑")
        try:
            edit_button.wait_for(state="visible", timeout=5000)
        except TimeoutError:
            pass
        page.get_by_role("button", name="下一步").click()

    
    def _select_date_in_picker(self, page, date_str: str) -> None:
        """
        在“请选择日期”的日期控件中选择指定日期。
        date_str 格式：YYYY-MM-DD
        """
        year, month, day = date_str.split("-")
        month_int = int(month)
        day_int = int(day)

        # 1. 打开日期控件
        date_box = page.get_by_role("textbox", name="请选择日期")
        date_box.wait_for(state="visible", timeout=5000)
        date_box.click()

        # 有些组件会有淡入动画，保证面板已出现
        page.get_by_text("年").first.wait_for(timeout=5000)

        # 2. 选择年份
        page.get_by_text("年").first.click()
        page.get_by_text(f"{year}年", exact=True).click()

        # 3. 选择月份
        page.get_by_text("月").first.click()
        page.get_by_text(f"{month_int}月", exact=True).click()

        # 4. 选择日（用 role=cell 更稳）
        page.get_by_role("cell", name=str(day_int)).click()

        # 5. 让焦点离开，触发表单校验
        page.keyboard.press("Tab")

    def _fill_soft_feature_form(
        self,
        page,
        task: Task,
        login_type: str,
        submit_role: str,
        log: LogFn,
    ) -> Path | None:
        #开发硬件环境
        self._log(log, "填写软件功能与特点")
        dev_hardware = task.meta.get("dev_hardware", "")
        page.locator("textarea").first.fill(f"{dev_hardware}")

        #运行硬件环境
        self._log(log, "填写运行硬件环境")
        run_hardware = task.meta.get("run_hardware", "")
        page.locator("div:nth-child(2) > .fillin_info > .hd-text-area > .large").fill(f"{run_hardware}")


        #开发该软件的操作系统
        self._log(log, "填写开发该软件的操作系统")
        dev_os = task.meta.get("dev_os", "")
        page.locator("div:nth-child(3) > .fillin_info > .hd-text-area > .large").fill(f"{dev_os}")

        #软件开发环境/开发工具
        self._log(log, "填写软件开发环境/开发工具")
        dev_tools = task.meta.get("dev_tools", "")
        page.locator("div:nth-child(4) > .fillin_info > .hd-text-area > .large").fill(f"{dev_tools}")


        #运行平台/操作系统
        self._log(log, "填写运行平台/操作系统")
        run_platform = task.meta.get("run_platform", "")
        page.locator("div:nth-child(5) > .fillin_info > .hd-text-area > .large").fill(f"{run_platform}")

        #运行支撑环境/支持软件
        self._log(log, "填写运行支撑环境/支持软件")
        support_software = task.meta.get("support_software", "")
        page.locator("div:nth-child(6) > .fillin_info > .hd-text-area > .large").fill(f"{run_platform}")

        #编程语言
        self._log(log, "填写编程语言")
        page.get_by_text("展开更多").click()
        langs = ["Assembly language", "C", "C#", "C++", "Delphi/Object Pascal", "Go", "HTML", 
                 "Java", "JavaScript", "MATLAB", "Objective-C", "PHP", "PL/SQL", "Perl", "Python", 
                 "R", "Ruby", "SQL", "Swift", "Visual Basic", "Visual Basic .Net"]
        for lang in langs:
            if lang.lower() in task.meta.get("programming_language", "").lower():
                page.get_by_text(lang, exact=True).click()

        #源程序量
        self._log(log, "填写源程序量")
        source_lines = task.meta.get("source_lines", "")
        page.locator("input[type=\"text\"]").fill(f"{source_lines}")

        #开发目的
        self._log(log, "填写开发目的")
        purpose = task.meta.get("purpose", "")
        page.locator("div:nth-child(9) > .fillin_info > .hd-text-area > .large").fill(f"{purpose}")

        #面向领域
        self._log(log, "填写面向领域")
        domain = task.meta.get("domain", "")
        page.locator("div:nth-child(10) > .fillin_info > .hd-text-area > .large").fill(f"{domain}")

        #主要功能
        self._log(log, "填写主要功能")
        main_features = task.meta.get("main_features", "")
        page.locator("div:nth-child(11) > .fillin_info > .hd-text-area > .large").fill(f"{main_features}")

        #技术特点
        self._log(log,"填写技术特点")
        page.get_by_text("信息安全软件").click()
        page.get_by_text("大数据软件").click()
        page.get_by_text("云计算软件").click()

        tech_features = task.meta.get("tech_features", "")
        page.locator("div:nth-child(12) > .fillin_info > .hd-text-area > .large").fill(f"{tech_features}")

        program_material = self._find_material_file(
            task,
            keyword_groups=[["源代码"], ["源码"]],
        )
        if not program_material:
            raise FileNotFoundError(f"未在 {task.extract_dir} 中找到包含“源代码”关键字的程序鉴别材料 PDF")
        page.get_by_text("程序鉴别材料").click()
        self._upload_material(page, page.locator(".hdUpload-item-ball > .icon").first, program_material)

        document_material = self._find_material_file(
            task,
            keyword_groups=[["说明书"], ["说明"], ["信息表"]],
            exclude={program_material},
        )
        if not document_material:
            raise FileNotFoundError(f"未在 {task.extract_dir} 中找到文档鉴别材料（说明书/信息表）PDF")
        doc_locator = page.locator(
            "div:nth-child(14) > .fillin_info > .IdentificationMaterial > "
            "div:nth-child(2) > .upLoadBox > .hdUpload > .hdUpload-imgBtn > .hdUpload-item-ball > .icon"
        ).first
        self._upload_material(page, doc_locator, document_material)
        page.wait_for_timeout(10000)  # 或者等待具体元素
        next_button = page.get_by_role("button", name="下一步")
        next_button.wait_for(state="visible", timeout=8000)
        next_button.click()
        page.wait_for_timeout(5000)  # 或者等待具体元素
        self._ensure_certificate_receive_address_selected(page, log)
        if submit_role == "申请人":
            submit_button = page.get_by_role("button", name="保存并提交申请")
            submit_button.wait_for(state="visible", timeout=6000)
            submit_button.click()
            self._log(log, "保存并提交申请完成")
        else:
            save_button = page.get_by_role("button", name="保存至草稿箱")
            save_button.wait_for(state="visible", timeout=6000)
            save_button.click()
            self._log(log, "保存草稿箱完成")
        page.wait_for_timeout(5000)  # 或者等待具体元素

        # 只有申请人模式才需要打印签章页，代理人保存草稿可以直接结束
        sign_pdf_path: Path | None = None
        if submit_role == "申请人":
            try:
                sign_pdf_path = self._generate_sign_page_pdf(page, task, log)
            except Exception as e:
                self._log(log, f"生成签章页 PDF 失败：{e}")
        return sign_pdf_path

    def _generate_sign_page_pdf(self, page: Page, task: Task, log: LogFn) -> Path:
        """
        从当前任务详情页：
        1）点击【打印签章页】打开材料列表页（新窗口）；
        2）在列表页点击底部【打印】，如果出现新窗口则使用新窗口，否则使用当前窗口；
        3）对签章页预览执行 page.pdf，生成签章页 PDF。
        """
        context = page.context

        self._log(log, "点击【打印签章页】，打开签章材料列表")

        # 1. 打开“打印签章页”列表窗口（确实是 popup）
        with page.expect_popup() as list_popup:
            page.get_by_role("button", name="打印签章页").click()
        sign_list_page = list_popup.value
        sign_list_page.wait_for_load_state("networkidle")
        self._log(log, f"签章材料列表页已打开，URL = {sign_list_page.url}")

        # 2. 在列表页点击底部【打印】
        self._log(log, "在签章材料列表页点击【打印】")

        # 点击前先记一下已有页面数量
        pages_before = context.pages.copy()

        # 这里不能再 expect_navigation，会卡死
        sign_list_page.get_by_role("button", name="打印").click()

        # 等一小会，看是否出现新窗口 / 新标签页
        sign_list_page.wait_for_timeout(3000)
        pages_after = context.pages.copy()

        sign_page: Page
        if len(pages_after) > len(pages_before):
            # 出现了新窗口，用最后一个
            sign_page = pages_after[-1]
            self._log(log, f"检测到签章页以新窗口形式打开，URL = {sign_page.url}")
        else:
            # 没有新窗口，说明是在当前页里直接变成预览
            sign_page = sign_list_page
            self._log(log, f"签章页在当前列表页面中渲染，当前 URL = {sign_page.url}")

        # 尽量等资源加载完
        try:
            sign_page.wait_for_load_state("networkidle", timeout=15000)
        except Exception as e:
            self._log(log, f"等待签章页网络空闲超时，继续尝试导出 PDF：{e}")

        # 输出目录 & 文件名
        output_root = self._locate_material_root(task) or task.extract_dir
        output_root.mkdir(parents=True, exist_ok=True)

        safe_name = "".join(
            ch if ch not in r'\/:*?"<>|' else "_"
            for ch in task.meta.get("software_name", "软件著作权")
        )
        pdf_path = output_root / f"{safe_name}_签章页.pdf"

        # 5. 生成 PDF（Chromium + headless=True 时效果最好）
        self._log(log, f"开始生成签章页 PDF：{pdf_path}")
        try:
            sign_page.pdf(
                path=str(pdf_path),
                format="A4",
                print_background=True,
            )
            self._log(log, "签章页 PDF 生成完成")
        except Exception as e:
            self._log(log, f"调用 page.pdf 失败：{e}")
            raise

        # 6. 收尾：关掉签章页窗口和列表页窗口（如果不是同一个）
        try:
            if sign_page is not sign_list_page:
                sign_page.close()
            sign_list_page.close()
        except Exception:
            pass

        return pdf_path

    def _resolve_short_name(self, task: Task) -> str:
        # meta_value = task.meta.get("软件简称") or task.meta.get("软件全称")
        # return str(meta_value or task.config.get("软件全称", ""))
        return ""

    def _upload_material(self, page, trigger, file_path: Path) -> None:
        with page.expect_file_chooser() as chooser_info:
            trigger.click()
        chooser_info.value.set_files(str(file_path))

    def _find_material_file(
        self,
        task: Task,
        keyword_groups: Sequence[Sequence[str]],
        exclude: set[Path] | None = None,
    ) -> Path | None:
        root = self._locate_material_root(task)
        if not root or not root.exists():
            return None

        candidates: list[Path] = []
        search_dirs = [root]
        docs_dir = root / "docs"
        if docs_dir.exists():
            search_dirs.append(docs_dir)
        for directory in search_dirs:
            candidates.extend(sorted(directory.glob("*.pdf")))

        if exclude:
            candidates = [path for path in candidates if path not in exclude]

        for keywords in keyword_groups:
            for path in candidates:
                if all(keyword in path.name for keyword in keywords):
                    return path
        return candidates[0] if candidates else None

    def _locate_material_root(self, task: Task) -> Path | None:
        base = task.extract_dir
        if not base or not base.exists():
            return None
        direct = base / "software_copyright_output"
        if direct.exists():
            return direct
        for child in base.iterdir():
            if child.is_dir():
                candidate = child / "software_copyright_output"
                if candidate.exists():
                    return candidate
        return base

    def _log(self, log: LogFn, message: str) -> None:
        if log:
            log(message)

    def _ensure_certificate_receive_address_selected(self, page: Page, log: LogFn) -> None:
        """
        证书领取地址列表有时不会自动选中默认联系人，这里强制点一次名单项，避免后台校验“未选择证书领取地址”。
        """
        # 先确认页面滚动/渲染到了证书领取区域
        markers = ["新增联系地址"]
        marker_found = False
        for text in markers:
            locator = page.get_by_text(text, exact=False)
            if locator.count() == 0:
                continue
            try:
                locator.first.wait_for(state="visible", timeout=4000)
                marker_found = True
                break
            except TimeoutError:
                continue

        if not marker_found:
            self._log(log, "未检测到证书领取地址区域，可能页面结构已变更，跳过自动点击")
            return

        def _is_empty_hint_visible() -> bool:
            try:
                page.get_by_text("暂无数据", exact=False).first.wait_for(state="visible", timeout=500)
                return True
            except TimeoutError:
                return False

        empty_hint = _is_empty_hint_visible()

        if empty_hint:
            self._log(log, "检测到证书领取地址为空状态，准备自动新增")
            if not self._create_default_certificate_address(page, log):
                self._log(log, "证书领取地址列表为空，且自动新增失败，可能需要人工处理")
                return
            page.wait_for_timeout(3000)
            try:
                page.get_by_role("listitem").filter(has_text=DEFAULT_CERT_CONTACT_NAME).click()
            except Exception:
                self._log(log, "点击新增联系人“朱砂”失败，可能需要人工确认")
                return
            self._log(log, f"点击证书领取地址：{DEFAULT_CERT_CONTACT_NAME}")
            page.wait_for_timeout(500)
            return

        try:
            marker = page.get_by_text("新增联系地址", exact=False).first
            marker.wait_for(state="visible", timeout=3000)
            target = marker.locator("xpath=following::li[1]")
            target.wait_for(state="visible", timeout=3000)
            full_text = target.inner_text().strip()
            clicked_name = full_text.splitlines()[0].strip() if full_text else "默认联系人"
            self._log(log, f"点击证书领取地址：{clicked_name}")
            page.get_by_role("listitem").filter(has_text=clicked_name).click()

        except Exception:
            self._log(log, "点击联系人列表失败，可能需要人工确认")
            return

        self._log(log, f"点击证书领取地址：{clicked_name}")
        page.wait_for_timeout(500)

    def _create_default_certificate_address(self, page: Page, log: LogFn) -> bool:
        """
        当证书领取地址为空时，自动新增一个默认地址，减少人工干预。
        """
        page.get_by_text("+新增联系地址").click()
        page.get_by_role("textbox", name="收件人（请填写真实姓名）").click()
        page.get_by_role("textbox", name="收件人（请填写真实姓名）").fill("朱砂")
        page.get_by_role("textbox", name="收件单位").click()
        page.get_by_role("textbox", name="收件单位").fill("中国江苏徐州邳州市东湖街道汉府西侧环球新天地二栋")
        page.locator(".box.large").click()
        page.locator(".hd-option").first.click()
        page.locator(".label").click()
        page.get_by_text("江苏", exact=True).click()
        page.get_by_text("徐州", exact=True).click()
        page.get_by_text("邳州市", exact=True).click()
        page.get_by_role("textbox", name="详细地址").click()
        page.get_by_role("textbox", name="详细地址").fill("中国江苏徐州邳州市东湖街道汉府西侧环球新天地二栋")
        page.get_by_role("textbox", name="邮编").click()
        page.get_by_role("textbox", name="邮编").fill("221300")
        page.get_by_role("textbox", name="请输入手机号码").click()
        page.get_by_role("textbox", name="请输入手机号码").fill("18168245213")
        page.get_by_role("button", name="保存", exact=True).click()
        return True
     
