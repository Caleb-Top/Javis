"""摄像头工具"""
import cv2, base64, logging
from typing import Optional
from core.tool_result import ToolResult
from utils.error_messages import friendly_error

logger = logging.getLogger("tools.camera")

def camera_snapshot(device_id: int = 0) -> ToolResult:
    """拍摄摄像头照片"""
    try:
        cap = cv2.VideoCapture(device_id, cv2.CAP_DSHOW)
        if not cap.isOpened():
            return ToolResult.failure(f"摄像头 {device_id} 未找到或被占用")

        ret, frame = cap.read()
        cap.release()

        if not ret:
            return ToolResult.failure("摄像头读取失败")

        # JPEG 编码 -> base64
        _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
        b64 = base64.b64encode(buf).decode()

        h, w = frame.shape[:2]
        return ToolResult(
            success=True,
            data=f"📷 摄像头拍照: {w}x{h}px",
            image=b64
        )
    except Exception as e:
        logger.warning(f"camera_snapshot 异常: {e}")
        return ToolResult.failure(friendly_error(e))

def camera_list(max_check: int = 5) -> ToolResult:
    """列出可用摄像头"""
    available = []
    import cv2
    for i in range(min(max_check, 5)):
        try:
            cap = cv2.VideoCapture(i, cv2.CAP_DSHOW)
            if cap.isOpened():
                ret, _ = cap.read()
                if ret:
                    available.append(f"摄像头 {i}")
                cap.release()
        except Exception:
            pass
    if available:
        return ToolResult.success("\n".join(available))
    return ToolResult.failure("未找到可用摄像头")
