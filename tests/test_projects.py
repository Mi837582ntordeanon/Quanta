import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from assistant import storage


class ProjectStoreTests(unittest.TestCase):
    def make_store(self, temp_dir):
        data_dir = Path(temp_dir) / "data"
        chats_dir = Path(temp_dir) / "chats"
        data_dir.mkdir()
        chats_dir.mkdir()
        patches = (
            patch.object(storage, "DATA_DIR", data_dir),
            patch.object(storage, "CHATS_DIR", chats_dir),
        )
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        return storage.Store()

    def test_project_context_follows_linked_chat_and_chat_deletion_cleans_link(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = self.make_store(temp_dir)
            chat = store.create_chat("Развитие Кванты")
            project = store.create_project(
                "Развитие Кванты", "Сделать Кванту устойчивым локальным помощником",
                next_step="Добавить слой проектов",
                decisions=["Данные хранятся локально"],
                files=[r"C:\Quanta\README.md"],
            )

            store.link_chat_to_project(project["id"], chat["id"])

            linked = store.get_project_for_chat(chat["id"])
            self.assertEqual(linked["id"], project["id"])
            listed = store.list_projects()[0]
            self.assertEqual(listed["chats"][0]["title"], "Развитие Кванты")

            store.delete_chat(chat["id"])
            self.assertIsNone(store.get_project_for_chat(chat["id"]))
            self.assertEqual(store.get_project(project["id"])["chat_ids"], [])

    def test_project_updates_and_archive_filter(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = self.make_store(temp_dir)
            project = store.create_project("Сайт", "Подготовить сайт", next_step="Собрать макет")
            updated = store.update_project(
                project["id"], status="paused", next_step="Сверить требования",
                add_decision="Сначала согласовать структуру",
            )

            self.assertEqual(updated["status"], "paused")
            self.assertEqual(updated["next_step"], "Сверить требования")
            self.assertIn("Сначала согласовать структуру", updated["decisions"])

            store.archive_project(project["id"])
            self.assertEqual(store.list_projects(), [])
            archived = store.list_projects(include_archived=True)
            self.assertEqual(archived[0]["status"], "archived")
            store.archive_project(project["id"], archived=False)
            self.assertEqual(store.list_projects()[0]["status"], "active")

    def test_linking_chat_to_another_project_moves_it(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = self.make_store(temp_dir)
            chat = store.create_chat("Общий чат")
            first = store.create_project("Первый", "Цель первая")
            second = store.create_project("Второй", "Цель вторая")

            store.link_chat_to_project(first["id"], chat["id"])
            store.link_chat_to_project(second["id"], chat["id"])

            self.assertEqual(store.get_project_for_chat(chat["id"])["id"], second["id"])
            self.assertEqual(store.get_project(first["id"])["chat_ids"], [])
            self.assertEqual(store.get_project(second["id"])["chat_ids"], [chat["id"]])

    def test_archived_project_must_be_restored_before_linking(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = self.make_store(temp_dir)
            chat = store.create_chat("Чат")
            project = store.create_project("Архив", "Цель")
            store.archive_project(project["id"])

            with self.assertRaisesRegex(ValueError, "из архива"):
                store.link_chat_to_project(project["id"], chat["id"])

            self.assertIsNone(store.get_project_for_chat(chat["id"]))


if __name__ == "__main__":
    unittest.main()
