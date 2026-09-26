"""Unit tests for IndexedStore: consistency, migration, stability, edges."""

import unittest

from indexed_store import IndexedStore


def make_store():
    return IndexedStore(indexed_fields=("age", "city"))


class TestBasicCRUD(unittest.TestCase):
    def test_put_get_delete(self):
        s = make_store()
        s.put(1, {"age": 30, "city": "bj"})
        self.assertEqual(s.get(1), {"age": 30, "city": "bj"})
        self.assertTrue(s.delete(1))
        self.assertFalse(s.delete(1))
        self.assertIsNone(s.get(1))
        self.assertEqual(len(s), 0)

    def test_empty_store_queries(self):
        s = make_store()
        self.assertEqual(s.query_eq("age", 30), [])
        self.assertEqual(s.query_range("age", 0, 100), [])
        self.assertEqual(s.query_range("city"), [])
        self.assertEqual(len(s), 0)

    def test_single_record(self):
        s = make_store()
        s.put("only", {"age": 1, "city": "sh"})
        self.assertEqual([pk for pk, _ in s.query_eq("age", 1)], ["only"])
        self.assertEqual([pk for pk, _ in s.query_range("age", 0, 2)], ["only"])
        self.assertEqual(s.query_eq("age", 2), [])
        s.delete("only")
        self.assertEqual(s.query_eq("age", 1), [])
        self.assertEqual(s.index_entries("age"), [])

    def test_query_on_unindexed_field_raises(self):
        s = make_store()
        with self.assertRaises(KeyError):
            s.query_eq("name", "x")
        with self.assertRaises(KeyError):
            s.query_range("name", "a", "z")


class TestIndexKeyMigration(unittest.TestCase):
    """Changing an indexed field must move the entry: no residue under
    the old key, presence under the new key."""

    def test_migration_via_put_overwrite(self):
        s = make_store()
        s.put(1, {"age": 30, "city": "bj"})
        s.put(2, {"age": 30, "city": "sh"})
        # Migrate pk=1 from age 30 -> 40.
        s.put(1, {"age": 40, "city": "bj"})
        self.assertEqual([pk for pk, _ in s.query_eq("age", 30)], [2])
        self.assertEqual([pk for pk, _ in s.query_eq("age", 40)], [1])
        # Raw index must not contain the stale (30, 1) entry.
        self.assertNotIn((30, 1), s.index_entries("age"))
        self.assertIn((40, 1), s.index_entries("age"))
        self.assertEqual(len(s.index_entries("age")), 2)

    def test_migration_via_update(self):
        s = make_store()
        s.put(1, {"age": 30, "city": "bj"})
        s.update(1, age=41)
        self.assertEqual(s.query_eq("age", 30), [])
        self.assertEqual([pk for pk, _ in s.query_eq("age", 41)], [1])
        self.assertEqual(s.query_range("age", 30, 40), [])
        self.assertEqual([pk for pk, _ in s.query_range("age", 40, 50)], [1])

    def test_migration_to_and_from_none(self):
        s = make_store()
        s.put(1, {"age": 30, "city": "bj"})
        s.update(1, age=None)  # leaves the index
        self.assertEqual(s.query_eq("age", 30), [])
        self.assertEqual(s.index_entries("age"), [])
        s.update(1, age=35)  # re-enters the index
        self.assertEqual([pk for pk, _ in s.query_eq("age", 35)], [1])
        self.assertEqual(s.index_entries("age"), [(35, 1)])

    def test_migration_same_value_is_noop(self):
        s = make_store()
        s.put(1, {"age": 30, "city": "bj"})
        s.update(1, city="sh")  # age unchanged
        self.assertEqual([pk for pk, _ in s.query_eq("age", 30)], [1])
        self.assertEqual(s.index_entries("age"), [(30, 1)])
        self.assertEqual(s.index_entries("city"), [("sh", 1)])

    def test_delete_removes_all_index_entries(self):
        s = make_store()
        s.put(1, {"age": 30, "city": "bj"})
        s.delete(1)
        self.assertEqual(s.index_entries("age"), [])
        self.assertEqual(s.index_entries("city"), [])


class TestDuplicateValuesStableOrder(unittest.TestCase):
    def test_duplicates_ordered_by_pk(self):
        s = make_store()
        # Insert in scrambled pk order, all with the same age.
        for pk in (5, 1, 9, 3, 7):
            s.put(pk, {"age": 30, "city": "bj"})
        pks = [pk for pk, _ in s.query_eq("age", 30)]
        self.assertEqual(pks, [1, 3, 5, 7, 9])

    def test_order_stable_across_mutations(self):
        s = make_store()
        for pk in (5, 1, 9, 3, 7):
            s.put(pk, {"age": 30, "city": "bj"})
        s.delete(5)
        s.put(4, {"age": 30, "city": "bj"})
        s.update(9, age=31)
        s.put(2, {"age": 30, "city": "bj"})
        pks = [pk for pk, _ in s.query_eq("age", 30)]
        self.assertEqual(pks, [1, 2, 3, 4, 7])
        # Range query must agree with equality query ordering.
        range_pks = [pk for pk, _ in s.query_range("age", 30, 30)]
        self.assertEqual(range_pks, pks)

    def test_range_ordering_by_value_then_pk(self):
        s = make_store()
        s.put(3, {"age": 20, "city": "bj"})
        s.put(1, {"age": 30, "city": "bj"})
        s.put(2, {"age": 20, "city": "bj"})
        got = [(pk, rec["age"]) for pk, rec in s.query_range("age", 0, 100)]
        self.assertEqual(got, [(2, 20), (3, 20), (1, 30)])


class TestNoneAndMissingIndexField(unittest.TestCase):
    def test_none_value_not_indexed(self):
        s = make_store()
        s.put(1, {"age": None, "city": "bj"})
        s.put(2, {"age": 30, "city": None})
        s.put(3, {"city": "sh"})  # age missing entirely
        self.assertEqual(s.query_eq("age", None), [])
        self.assertEqual([pk for pk, _ in s.query_range("age", 0, 100)], [2])
        self.assertEqual([pk for pk, _ in s.query_range("city", "a", "z")], [1, 3])
        # Records still live in the primary table.
        self.assertEqual(len(s), 3)
        self.assertEqual(s.get(3), {"city": "sh"})


class TestBatchDelete(unittest.TestCase):
    def test_delete_many(self):
        s = make_store()
        for pk in range(10):
            s.put(pk, {"age": pk * 10, "city": "bj" if pk % 2 else "sh"})
        removed = s.delete_many([0, 2, 4, 6, 8, 999])  # 999 does not exist
        self.assertEqual(removed, 5)
        self.assertEqual(len(s), 5)
        self.assertEqual(s.query_range("age", 0, 1000),
                         [(pk, s.get(pk)) for pk in (1, 3, 5, 7, 9)])
        self.assertEqual([pk for pk, _ in s.query_eq("city", "bj")],
                         [1, 3, 5, 7, 9])
        self.assertEqual(s.query_eq("city", "sh"), [])
        # Raw index sizes must match the table size.
        self.assertEqual(len(s.index_entries("age")), 5)
        self.assertEqual(len(s.index_entries("city")), 5)

    def test_delete_all_leaves_empty_indexes(self):
        s = make_store()
        for pk in range(100):
            s.put(pk, {"age": pk, "city": "bj"})
        s.delete_many(list(range(100)))
        self.assertEqual(len(s), 0)
        self.assertEqual(s.index_entries("age"), [])
        self.assertEqual(s.index_entries("city"), [])


class TestRangeBounds(unittest.TestCase):
    def test_inclusive_exclusive_bounds(self):
        s = make_store()
        for pk in range(5):
            s.put(pk, {"age": pk * 10, "city": "bj"})
        eq = lambda lo, hi, il, ih: [pk for pk, _ in s.query_range(
            "age", lo, hi, include_lo=il, include_hi=ih)]
        self.assertEqual(eq(10, 30, True, True), [1, 2, 3])
        self.assertEqual(eq(10, 30, False, True), [2, 3])
        self.assertEqual(eq(10, 30, True, False), [1, 2])
        self.assertEqual(eq(10, 30, False, False), [2])
        self.assertEqual(eq(None, 20, True, True), [0, 1, 2])
        self.assertEqual(eq(30, None, True, True), [3, 4])


if __name__ == "__main__":
    unittest.main(verbosity=2)
