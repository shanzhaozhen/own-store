"""所有面向用户的中文文案。

界面代码里不许写死字符串 —— 集中在这里才能一处统一审校语气。

写文案的规矩（详见 docs/06-界面规范.md）：
- 说清楚"要你做什么"，不说"哪里出错了"
- 不出现英文、错误码、技术术语
- 用"要做的事"描述功能，不用功能名（「照片变清楚再打印」而不是「图像增强」）
"""

from __future__ import annotations

from enum import Enum, auto

APP_TITLE = "打印助手"

# ── 首页四张大卡片 ────────────────────────────────────────────────
HOME_CARD_PRINT_TITLE = "打印文档"
HOME_CARD_PRINT_HINT = "Word Excel PDF 都行"
HOME_CARD_PHOTO_TITLE = "照片变清楚再打印"
HOME_CARD_PHOTO_HINT = "拍照的文件背景发灰，用这个"
HOME_CARD_OCR_TITLE = "照片转成文字文档"
HOME_CARD_OCR_HINT = "转成可以修改的 Word"
HOME_CARD_WORKSPACE_TITLE = "工作区"
HOME_CARD_WORKSPACE_HINT = "微信收到的、放进工作区的文件都在这里"
HOME_CARD_CARDS_TITLE = "证件印一张纸"
HOME_CARD_CARDS_HINT = "身份证正反面、户口本两页"
HOME_CARD_PASTE_TITLE = "粘贴刚复制的图片"
HOME_CARD_PASTE_HINT = "在微信里右键复制，回来点这个"
HOME_DROP_HINT = "或者把文件直接拖进来"

# ── 通用按钮 ──────────────────────────────────────────────────────
BTN_BACK = "← 返回"
BTN_START_PRINT = "开 始 打 印"
BTN_START_OCR = "开 始 转 换"
BTN_CHOOSE_FILE = "选择文件"
BTN_PASTE_IMAGE = "粘贴图片"
BTN_OPEN_FOLDER = "打开文件夹"
BTN_SAVE = "保存"
BTN_RETRY = "再试一次"
BTN_CANCEL = "取消"
# 页面上那块"把图片放这里"的空地。首页卡片点进来先到页面，再在这里选图
PICK_IMAGE_HINT = "点这里选图片\n也可以把图片拖进来"
PICK_ANOTHER_HINT = "点一下换一张图片，也可以直接把新图片拖进来"

# ── 打印页 ────────────────────────────────────────────────────────
PRINT_TITLE = "打印预览"
LABEL_COPIES = "打几份"
LABEL_SIDES = "单面还是双面"
SIDES_SINGLE = "单面"
SIDES_DOUBLE = "双面"
LABEL_PAPER = "纸张"
LABEL_PRINT_ZOOM = "大小"
PRINT_ZOOM_HINT = "100 = 刚好铺满纸；打出来超边就调小一点"
LABEL_COLOR_DISABLED = "本店打印机只有黑白"
LABEL_PRINTER = "用哪台打印机"
PRINTER_NONE = "（还没找到打印机）"
PRINT_ACTUAL_SIZE = "这一张按证件的实际大小打，不缩放"

# ── 照片增强页 ────────────────────────────────────────────────────
MODE_AUTO = "自动"
MODE_TEXT = "文字为主"
MODE_MIXED = "图文混排"
MODE_PHOTO = "照片"
MODE_AUTO_HINT = "不确定就用这个"
MODE_TEXT_HINT = "只有文字的合同、证明、作业"
MODE_MIXED_HINT = "有照片、印章、手写的"
MODE_PHOTO_HINT = "顾客要打的就是照片本身"
LABEL_STRENGTH = "效果强弱"
STRENGTH_LIGHT = "淡"
STRENGTH_HEAVY = "浓"
STRENGTH_HINT = "字太淡就往右拉，背景发脏就往左拉"
LABEL_BEFORE = "原来的"
LABEL_AFTER = "处理后"
LABEL_COLOR_MODE = "黑白还是彩色"
COLOR_MONO = "黑白"
COLOR_COLOR = "彩色"
COLOR_HINT_PRINT_MONO = "打印只有黑白；彩色给另存为用"
PHOTO_SAVE_IMAGE = "保存成图片"
PHOTO_SAVE_PDF = "保存成 PDF"
PAPER_DETECTED = "认出是整张 A4/A3 纸，已经裁正拉平"
LABEL_CROP = "裁剪"
CROP_AUTO = "自动裁正"
CROP_OFF = "不裁"
LABEL_CROP_MARGIN = "边缘"
CROP_TIGHT = "裁多点"
CROP_LOOSE = "留多点"
CROP_HINT = "裁到内容了就把「边缘」往右拉"

# ── 证件二合一页 ──────────────────────────────────────────────────
CARDS_TITLE = "证件印一张纸"
CARDS_TYPE_LABEL = "这是什么证件"
CARDS_TYPE_AUTO = "自动认"
CARDS_TYPE_AUTO_HINT = "认不准的时候自己点上面的类型"
CARDS_PASTE = "粘贴"
CARDS_ROTATE = "转一下"
CARDS_CLEAR = "去掉"
CARDS_SLOT_LOADING = "正在打开…"
CARDS_NEED_TWO = "还差一张：{}"
CARDS_SAVE_PDF = "保存成 PDF"
CARDS_SAVE_AS = "另存为…"
CARDS_STRENGTH_LABEL = "深浅"
CARDS_STRENGTH_HINT = "字看不清就往右拉，底纹太脏就往左拉"
BUSY_CARDS = "正在处理证件照片，请稍等…"
BUSY_CARDS_TONE = "正在按新的深浅重画…"

# ── 工作区页（店里放待打印文件的文件夹 + 微信收到的文件）──────────
WORKSPACE_OPEN_FOLDER = "打开工作区文件夹"
WORKSPACE_EMPTY = (
    "工作区里还没有文件。\n\n"
    "顾客在微信里发过来的文件会自动出现在这里，\n"
    "存到工作区文件夹里的文件也会。\n"
    "如果发来的是聊天里的图片，请在微信里右键点图片、选「复制」，\n"
    "再回到这里点下面的「粘贴图片」。"
)

# ── 进度与结果（要又大又明确）────────────────────────────────────
BUSY_PROCESSING = "正在处理，请稍等…"
BUSY_CONVERTING = "正在准备文件，请稍等…"
BUSY_RECOGNIZING = "正在识别文字，请稍等…"
DONE_PRINTED = "打印好了 ✓"
DONE_SAVED = "已经保存好了 ✓"
DONE_OCR = "已经转好了，请核对一下文字有没有认错"
OCR_LOW_CONFIDENCE_HINT = "标红的字可能认错了，请核对"


def printing_progress(current: int, total: int) -> str:
    return f"正在打印第 {current} 页 / 共 {total} 页"


def recognizing_progress(current: int, total: int) -> str:
    return f"正在识别第 {current} 张 / 共 {total} 张"


def page_count(total: int) -> str:
    return f"共 {total} 页"


class ErrorKind(Enum):
    """出错的种类。每一种都有一句人话提示，技术细节只写日志。"""

    PRINTER_OFFLINE = auto()
    PRINTER_NOT_FOUND = auto()
    FILE_BROKEN = auto()
    FILE_ENCRYPTED = auto()
    FILE_UNSUPPORTED = auto()
    FILE_TOO_LARGE = auto()
    OFFICE_MISSING = auto()
    OFFICE_TIMEOUT = auto()
    OCR_EMPTY = auto()
    NO_IMAGE_IN_CLIPBOARD = auto()
    DISK_FULL = auto()
    UNKNOWN = auto()


_FRIENDLY_ERRORS: dict[ErrorKind, str] = {
    ErrorKind.PRINTER_OFFLINE: "打印机没有连上。\n请看看它的电源开了没有、线插好了没有，然后再试一次。",
    ErrorKind.PRINTER_NOT_FOUND: "电脑上找不到打印机。\n请检查一下数据线，或者叫人来看看。",
    ErrorKind.FILE_BROKEN: "这个文件打不开，可能是坏了。\n请让顾客重新发一次。",
    ErrorKind.FILE_ENCRYPTED: "这个文件有密码，打不开。\n请让顾客发一个没有密码的。",
    ErrorKind.FILE_UNSUPPORTED: "这种文件暂时打不了。\n请让顾客发成 PDF 或者图片。",
    ErrorKind.FILE_TOO_LARGE: "这个文件太大了，处理不了。\n请让顾客分成几份发过来。",
    ErrorKind.OFFICE_MISSING: "这台电脑上的 Word 好像有问题，打不开这个文件。\n请叫人来看看。",
    ErrorKind.OFFICE_TIMEOUT: "这个文件处理太久了，可能有问题。\n请让顾客重新发一次，或者发成 PDF。",
    ErrorKind.OCR_EMPTY: "这张照片上没认出文字，可能太模糊了。\n请重新拍一张亮一点、平一点的。",
    ErrorKind.NO_IMAGE_IN_CLIPBOARD: "没有找到复制的图片。\n请先在微信里右键点图片、选「复制」，再回来点这个按钮。",
    ErrorKind.DISK_FULL: "电脑硬盘快满了，存不下文件。\n请叫人来清理一下。",
    ErrorKind.UNKNOWN: "出了点问题，已经记录下来了。\n请重试一次，还是不行就叫人来看看。",
}


def friendly_error(kind: ErrorKind) -> str:
    """把错误种类翻译成一句长辈能看懂、并且知道下一步做什么的话。"""
    return _FRIENDLY_ERRORS.get(kind, _FRIENDLY_ERRORS[ErrorKind.UNKNOWN])
