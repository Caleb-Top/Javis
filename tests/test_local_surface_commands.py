import unittest

from utils.local_surface_commands import match_local_surface_command


class LocalSurfaceCommandTests(unittest.TestCase):
    def test_explicit_native_surface_commands_are_recognized(self):
        self.assertEqual(match_local_surface_command("打开 Code 页面"), "code")
        self.assertEqual(match_local_surface_command("进入设置"), "settings")
        self.assertEqual(match_local_surface_command("运行诊断"), "diagnostics")
        self.assertEqual(match_local_surface_command("回到交流球"), "live")

    def test_real_agent_tasks_are_not_intercepted(self):
        self.assertIsNone(match_local_surface_command("帮我写一段 Python 代码"))
        self.assertIsNone(match_local_surface_command("检查项目文件并修复问题"))
        self.assertIsNone(match_local_surface_command("在终端运行全部测试"))


if __name__ == "__main__":
    unittest.main()
