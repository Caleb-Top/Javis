import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ROOT = PROJECT_ROOT / "app" / "src"


class AppPhase2Tests(unittest.TestCase):
    def read(self, relative: str) -> str:
        return (ROOT / relative).read_text(encoding="utf-8")

    def test_runtime_state_has_single_coordinator_and_request_scoped_terminal_rules(self):
        types = self.read("state/runtimeStateTypes.ts")
        coordinator = self.read("state/RuntimeStateCoordinator.ts")

        self.assertIn("StateSignal", types)
        self.assertIn('source: "sidecar" | "websocket" | "voice" | "tool" | "permission" | "ui"', types)
        self.assertIn("class RuntimeStateCoordinator", coordinator)
        self.assertIn("signal(", coordinator)
        self.assertIn("subscribe(", coordinator)
        self.assertIn("requestId", coordinator)
        self.assertIn("terminal", coordinator)
        self.assertIn("STATE_PRIORITY", coordinator)

    def test_request_queue_is_bounded_deduplicated_and_has_finite_reconnect_schedule(self):
        queue = self.read("bridge/requestQueue.ts")
        backend = self.read("bridge/backendClient.ts")

        self.assertIn("MAX_QUEUE_SIZE = 32", queue)
        self.assertIn("requestId", queue)
        self.assertIn("dedupeKey", queue)
        self.assertIn("cancel", queue)
        self.assertIn("[1000, 2000, 3000, 5000, 8000, 15000]", backend)
        self.assertNotIn("setTimeout(() => send(clean), 250)", backend)

    def test_live_stage_uses_status_rail_caption_and_multiline_composer(self):
        main = self.read("main.ts")
        stage = self.read("live/LiveStage.ts")
        composer = self.read("live/CommandComposer.ts")
        caption = self.read("live/LiveCaption.ts")
        rail = self.read("panels/StatusRail.ts")
        preferences = self.read("app/AppPreferences.ts")

        self.assertIn("renderLiveStage", main)
        self.assertIn("createStatusRail", main)
        self.assertIn("createCommandComposer", main)
        self.assertIn("createLiveCaption", main)
        self.assertIn("<textarea", stage)
        self.assertIn('aria-live="polite"', stage)
        self.assertIn("isComposing", composer)
        self.assertIn("shiftKey", composer)
        self.assertIn("MAX_ROWS = 6", composer)
        self.assertIn("line-clamp", caption)
        self.assertIn("aria-expanded", rail)
        self.assertIn("writePreference", rail)
        self.assertIn("localStorage", preferences)

    def test_live_css_has_stable_tracks_breakpoints_and_accessibility_modes(self):
        css = self.read("styles.css")

        self.assertIn("grid-template-rows: auto minmax(0, 1fr) auto auto", css)
        self.assertIn("aspect-ratio: 1", css)
        self.assertIn("-webkit-line-clamp: 2", css)
        self.assertIn("overflow-wrap: anywhere", css)
        self.assertIn("@media (max-width: 899px)", css)
        self.assertIn("@media (min-width: 1200px)", css)
        self.assertIn("@media (prefers-reduced-motion: reduce)", css)
        self.assertIn("@media (forced-colors: active)", css)
        self.assertIn("max-height: 104px", css)
        self.assertIn('.live-stage:has(.status-rail[data-expanded="true"]) .orb-wrap', css)

    def test_tauri_window_enforces_orb_and_code_minimum_sizes(self):
        config = (PROJECT_ROOT / "app" / "src-tauri" / "tauri.conf.json").read_text(encoding="utf-8")
        mode = (PROJECT_ROOT / "app" / "src" / "desktop" / "windowMode.ts").read_text(encoding="utf-8")

        self.assertIn('"minWidth": 132', config)
        self.assertIn('"minHeight": 158', config)
        self.assertIn("new LogicalSize(760, 560)", mode)
        self.assertIn("new LogicalSize(1280, 820)", mode)


class AppPhase3Tests(unittest.TestCase):
    def read(self, relative: str) -> str:
        return (ROOT / relative).read_text(encoding="utf-8")

    def test_conversation_drawer_has_cancellable_fts_search_states(self):
        drawer = self.read("panels/ConversationDrawer.ts")

        self.assertIn("MIN_QUERY_LENGTH = 2", drawer)
        self.assertIn("SEARCH_DEBOUNCE_MS = 250", drawer)
        self.assertIn("AbortController", drawer)
        self.assertIn("/api/memory/search", drawer)
        for state in ['"loading"', '"empty"', '"error"', '"ready"']:
            self.assertIn(state, drawer)

    def test_drawer_manager_allows_one_drawer_and_restores_focus(self):
        manager = self.read("panels/DrawerManager.ts")

        self.assertIn("activeDrawer", manager)
        self.assertIn('event.key === "Escape"', manager)
        self.assertIn("restoreFocus", manager)
        self.assertIn("close", manager)

    def test_control_drawer_connects_runtime_permission_task_and_perception_apis(self):
        control = self.read("panels/ControlDrawer.ts")
        tasks = self.read("panels/TaskStream.ts")

        for endpoint in [
            "/api/runtime/status",
            "/api/blueprint/coverage",
            "/api/config/permission",
            "/api/control/commands",
            "/api/perception/screen/analyze",
        ]:
            self.assertIn(endpoint, control + tasks)
        self.assertIn("仅分析当前屏幕，不保存原始截图", control)
        self.assertIn("safe", control)
        self.assertIn("trusted", control)
        self.assertIn("root", control)
        self.assertIn("confirm", control)

    def test_ui_rate_limiter_has_required_action_windows(self):
        limiter = self.read("security/RateLimiter.ts")

        self.assertIn("DEFAULT_ACTION_WINDOW_MS = 300", limiter)
        self.assertIn("VOICE_ACTION_WINDOW_MS = 600", limiter)
        self.assertIn("remaining", limiter)
        self.assertNotIn("while (", limiter)

    def test_main_mounts_phase_three_drawers(self):
        main = self.read("main.ts")

        self.assertIn("createDrawerManager", main)
        self.assertIn("createConversationDrawer", main)
        self.assertIn("createControlDrawer", main)
        self.assertIn("javis:open-conversations", main)


class AppPhase4Tests(unittest.TestCase):
    def read(self, relative: str) -> str:
        return (ROOT / relative).read_text(encoding="utf-8")

    def test_code_surface_hosts_the_existing_full_web_interface(self):
        surface = self.read("code/CodeSurface.ts")

        self.assertIn("resolveBackendEndpoints", surface)
        self.assertIn('url.searchParams.set("app_embed", "1")', surface)
        self.assertIn("legacy-web-frame", surface)
        self.assertIn('allow="fullscreen"', surface)
        self.assertNotIn("microphone", surface)
        self.assertNotIn("camera", surface)
        self.assertNotIn("clipboard-read", surface)
        self.assertIn("javis:return-live", surface)
        self.assertIn("code-web-refresh", surface)
        self.assertNotIn("将在这里按需出现", surface)

    def test_file_explorer_only_uses_controlled_workspace_apis(self):
        explorer = self.read("code/FileExplorer.ts")

        self.assertIn("/api/workspace/explore", explorer)
        self.assertIn("/api/workspace/read", explorer)
        self.assertIn("is_dir", explorer)
        self.assertNotIn("@tauri-apps/plugin-fs", explorer)

    def test_editor_tracks_dirty_state_conflict_and_save_failures(self):
        editor = self.read("code/EditorPane.ts")

        self.assertIn("dirty", editor)
        self.assertIn("expected_modified", editor)
        self.assertIn("conflict", editor)
        self.assertIn("/api/workspace/save", editor)
        self.assertIn("保存失败", editor)

    def test_terminal_runs_and_cancels_audited_command_tasks(self):
        terminal = self.read("code/TerminalPane.ts")

        self.assertIn("/api/control/commands/start", terminal)
        self.assertIn("/api/control/commands/", terminal)
        self.assertIn("/cancel", terminal)
        self.assertIn("exit_code", terminal)
        self.assertIn("root_token", terminal)

    def test_code_css_is_a_full_surface_web_host(self):
        css = self.read("styles.css")

        self.assertIn(".code-surface", css)
        self.assertIn(".legacy-web-frame", css)
        self.assertIn(".code-app-toolbar", css)
        self.assertIn("body[data-surface=\"code\"]", css)
        self.assertIn("@media (max-width: 899px)", css)


if __name__ == "__main__":
    unittest.main()
