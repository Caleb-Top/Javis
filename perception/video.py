"""Local video stream analysis for the perception subsystem."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable


@dataclass
class VideoSegment:
    index: int
    start_ts: float
    end_ts: float
    events: list[Any] = field(default_factory=list)

    def summary(self) -> str:
        parts = [str(getattr(event, "summary", "")).strip() for event in self.events]
        parts = [part for part in parts if part]
        if not parts:
            return f"Video segment {self.index}: no notable local perception events"
        unique = []
        for part in parts:
            if part not in unique:
                unique.append(part)
        return f"Video segment {self.index}: " + "; ".join(unique[:8])


class VideoStreamAnalyzer:
    """Runs change-gated local perception over video frames."""

    def __init__(
        self,
        perception: Any,
        change_threshold: float = 0.08,
        segment_seconds: float = 8.0,
        max_frames: int = 600,
    ):
        self.perception = perception
        self.change_threshold = max(0.0, min(1.0, float(change_threshold)))
        self.segment_seconds = max(0.25, float(segment_seconds))
        self.max_frames = max(1, int(max_frames))

    def analyze_frames(
        self,
        frames: Iterable[tuple[float, Any]],
        source: str = "video",
        adapters: list[str] | None = None,
        adapter_options: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        previous = None
        frames_seen = 0
        frames_analyzed = 0
        skipped_static = 0
        segments_created = 0
        current = VideoSegment(index=0, start_ts=0.0, end_ts=0.0)

        for ts, frame in frames:
            if frames_seen >= self.max_frames:
                break
            frames_seen += 1
            timestamp = float(ts or 0.0)
            if frames_seen == 1:
                current.start_ts = timestamp

            changed = previous is None or self._frame_delta(previous, frame) >= self.change_threshold
            if changed:
                events = self.perception.analyze_image(
                    image=frame,
                    source=f"{source}@{timestamp:.2f}s",
                    adapter_names=adapters,
                    adapter_options=adapter_options,
                )
                current.events.extend(events)
                frames_analyzed += 1
                previous = frame
            else:
                skipped_static += 1

            current.end_ts = timestamp
            if timestamp - current.start_ts >= self.segment_seconds and current.events:
                self._emit_segment(source, current)
                segments_created += 1
                current = VideoSegment(index=current.index + 1, start_ts=timestamp, end_ts=timestamp)

        if current.events:
            self._emit_segment(source, current)
            segments_created += 1

        return {
            "ok": True,
            "source": source,
            "frames_seen": frames_seen,
            "frames_analyzed": frames_analyzed,
            "skipped_static": skipped_static,
            "segments": segments_created,
            "change_threshold": self.change_threshold,
            "segment_seconds": self.segment_seconds,
        }

    def _emit_segment(self, source: str, segment: VideoSegment) -> Any:
        return self.perception.ingest(
            source=source,
            modality="vision.video_segment",
            summary=segment.summary(),
            confidence=self._segment_confidence(segment),
            metadata={
                "segment_index": segment.index,
                "start_ts": segment.start_ts,
                "end_ts": segment.end_ts,
                "event_ids": [getattr(event, "id", "") for event in segment.events],
                "event_count": len(segment.events),
            },
        )

    def _segment_confidence(self, segment: VideoSegment) -> float:
        if not segment.events:
            return 0.0
        confidences = [float(getattr(event, "confidence", 0.5)) for event in segment.events]
        return round(sum(confidences) / len(confidences), 3)

    @staticmethod
    def _frame_delta(previous: Any, current: Any) -> float:
        try:
            import numpy as np

            prev = np.asarray(previous, dtype="float32")
            cur = np.asarray(current, dtype="float32")
            if prev.shape != cur.shape:
                return 1.0
            return float(np.mean(np.abs(prev - cur)) / 255.0)
        except Exception:
            return 1.0 if previous is not current else 0.0
