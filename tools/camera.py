"""摄像头工具 — 拍照和基础分析"""

import base64
import io
import logging
from core.tool_result import ToolResult
from utils.error_messages import friendly_error

logger = logging.getLogger("tools.camera")


def camera_snapshot(device_id: int = 0, **kwargs) -> ToolResult:
    """摄像头拍照, 返回 base64"""
    try:
        import cv2
        cap = cv2.VideoCapture(device_id)
        if not cap.isOpened():
            return ToolResult.failure(f"无法打开摄像头 (设备 {device_id})")

        ret, frame = cap.read()
        cap.release()

        if not ret:
            return ToolResult.failure("摄像头读取失败")

        # JPEG 编码 → base64
        _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
        b64 = base64.b64encode(buf).decode()

        h, w = frame.shape[:2]
        return ToolResult(
            success=True,
            data=f"📷 摄像头拍照: {w}x{h}px",
            image=b64
        )
    except ImportError as e:
        return ToolResult.failure(friendly_error(e))
    except Exception as e:
        return ToolResult.failure(friendly_error(e))


def camera_list() -> ToolResult:
    """列出可用摄像头"""
    try:
        import cv2
        result = []
        for i in range(5):                          # 最多检测 5 个
            cap = cv2.VideoCapture(i)
            if cap.isOpened():
                w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                result.append(f"  Camera {i}: {w}x{h}")
                cap.release()
        if not result:
            return ToolResult.failure("未检测到可用摄像头")
        return ToolResult.success("可用摄像头:\n" + "\n".join(result))
    except Exception as e:
        return ToolResult.failure(f"摄像头检测失败: {e}")
