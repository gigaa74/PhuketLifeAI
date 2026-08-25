import unittest
from pathlib import Path

import bot
import client_ai
import client_handler


class BotModuleBoundaryTests(unittest.TestCase):
    def test_bot_entrypoint_is_below_previous_monolith_threshold(self):
        bot_path = Path(bot.__file__)
        self.assertLess(bot_path.stat().st_size, 100_000)

    def test_client_handler_is_reexported_without_changing_entrypoint_api(self):
        self.assertIs(bot.handle_message, client_handler.handle_message)
        self.assertIs(bot.ask_gigachat, client_ai.ask_gigachat)
        self.assertIs(bot.analyze_case, client_ai.analyze_case)

    def test_large_client_prompts_are_not_kept_in_bot_entrypoint(self):
        source = Path(bot.__file__).read_text(encoding="utf-8")
        self.assertNotIn("Ты анализируешь запрос клиента Phuket Life", source)
        self.assertNotIn("КРИТИЧЕСКОЕ ПРАВИЛО ДОСТОВЕРНОСТИ", source)


if __name__ == "__main__":
    unittest.main()
