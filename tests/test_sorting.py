import unittest

from backend.main import filter_sort_collection


class SortingTests(unittest.TestCase):
    def test_text_sort_ignores_case_and_keeps_missing_values_last(self):
        rows = [{"name": value} for value in ("zaza", None, "Akagera", "butare")]
        fields = {"name": "name"}
        ascending = filter_sort_collection(rows, None, [], "name", "asc", fields)
        descending = filter_sort_collection(rows, None, [], "name", "desc", fields)
        self.assertEqual([row["name"] for row in ascending],
                         ["Akagera", "butare", "zaza", None])
        self.assertEqual([row["name"] for row in descending],
                         ["zaza", "butare", "Akagera", None])

    def test_numeric_sort_uses_value_not_display_text(self):
        rows = [{"count": value} for value in (20, 3, 100)]
        fields = {"count": "count"}
        self.assertEqual(
            [row["count"] for row in filter_sort_collection(rows, None, [], "count", "asc", fields)],
            [3, 20, 100],
        )


if __name__ == "__main__":
    unittest.main()
