from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Callable, Sequence

from playwright.sync_api import Playwright, TimeoutError, sync_playwright

from models import DEFAULT_OWNER_TYPE, OWNER_TYPE_ID_OPTIONS, Task

AUTH_DIR = Path("playwright/.auth")
STORAGE_STATE = AUTH_DIR / "storage_state.json"

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

    def __init__(self, headless: bool = False):
        self.headless = headless

    def upload(self, task: Task, log: LogFn = None) -> None:
        self._log(log, f"开始上传：{task.display_name()}")
        ensure_storage_state_file()
        with sync_playwright() as playwright:
            self._run(playwright, task, log)
        self._log(log, "上传流程结束")

    def _run(self, playwright: Playwright, task: Task, log: LogFn) -> None:
        self._log(log, "启动浏览器")
        launch_args = ["--start-fullscreen"]
        browser = playwright.chromium.launch(headless=self.headless, args=launch_args)
        context = browser.new_context(storage_state=str(STORAGE_STATE))
        page = context.new_page()
        self._log(log, "打开官网")
        page.goto("https://register.ccopyright.com.cn/registration.html#/index")
        skip_button = page.get_by_text("跳过")
        if skip_button.count() > 0 and skip_button.is_visible():
            skip_button.click()
        page.locator(".soft").click()
        # with page.expect_popup() as page1_info:
        #     page.locator(".pic > a").first.click()
        # page1 = page1_info.value
        self._log(log, "进入办理页面")
        page.get_by_role("cell", name="R11").get_by_role("button").click()

        if self._login_if_needed(page, log):
            self._log(log, "登录成功，保存 storage_state")
            context.storage_state(path=str(STORAGE_STATE))

        # page1.get_by_role("heading", name="我是代理人").click()
        page.get_by_text("我是代理人 我为他人创作的软件申请著作权登记").click()
        page.get_by_role("button", name="确定").click()
        self._log(log, "填写软件申请信息")
        self._fill_basic_form(page, task)
        self._log(log, "填写软件开发信息")
        self._fill_soft_dev_info_form(page, task)
        self._log(log, "填写软件功能与特点")
        self._fill_soft_feature_form(page, task, log)
        self._log(log, "页面操作完成，关闭浏览器")
        context.close()
        browser.close()

    def _login_if_needed(self, page, log: LogFn) -> bool:
        """Return True if login occurred."""
        login_required = False
        username_input = page.get_by_role("textbox", name="请输入用户名/手机号/邮箱")
        try:
            username_input.wait_for(state="visible", timeout=10000)
            login_required = True
        except TimeoutError:
            login_required = False

        if login_required:
            self._log(log, "检测到登录界面，执行登录流程")
            page.get_by_text("机构", exact=True).click()
            username_input.click()
            username_input.fill("Yf19942050676_")
            password_input = page.get_by_role("textbox", name="请输入密码")
            password_input.click()
            password_input.fill("Yf19942050676_")
            page.get_by_role("button", name="立即登录").click()

            button = page.get_by_role("cell", name="R11").get_by_role("button")
            button.wait_for(state="visible", timeout=300000)
            button.click()
            return True
        self._log(log, "保持登录状态，无需重新登录")
        return False

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

    def _fill_soft_dev_info_form(self, page, task: Task) -> None:
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
            date_input = page.locator("div.datepicker-input > input")
            date_input.wait_for(state="visible", timeout=5000)
            date_input.evaluate("el => el.removeAttribute('readonly')")
            date_input.evaluate("el => el.removeAttribute('disabled')")
            date_input.click()
            date_input.fill(completion_date)
        # page.locator(".right > .icon > use").click()
        # # 这个地方等待日期选择框中的今天显示出来再操作, page.get_by_role("link", name="今天")
        # page.locator(".datePickerSelectText > .icon").first.click()
        # today_link = page.get_by_role("link", name="今天")
        # today_link.wait_for(state="visible", timeout=5000)
        # page.get_by_text(f"{year}年").click()
        # page.locator("div:nth-child(2) > .datePickerSelectText > .icon").click()
        # page.get_by_text(f"{month}月", exact=True).click()
        # page.get_by_text(f"{day}", exact=True).first.click()
        # 在这个地方等待page.locator(".remove > .icon > use")这个元素出现再继续
        remove_icon = page.locator(".remove > .icon > use")
        remove_icon.wait_for(state="visible", timeout=5000)
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

    def _fill_soft_feature_form(self, page, task: Task, log: LogFn) -> None:
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
        next_button.wait_for(state="visible", timeout=20000)
        next_button.click()
        page.wait_for_timeout(1000)  # 或者等待具体元素
        page.get_by_role("button", name="保存至草稿箱").wait_for(state="visible", timeout=20000)
        page.get_by_role("button", name="保存至草稿箱").click()
        self._log(log, "保存草稿箱完成")

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
