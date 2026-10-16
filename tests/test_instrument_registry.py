import unittest
from unittest.mock import MagicMock, patch
from fastapi import HTTPException
from fastapi.testclient import TestClient
from backend import main
from backend.instrument_registry import check_registry_change
from backend.catalog_registry_20261015 import registry_records
from backend.instrument_availability import load_inventory


class InstrumentRegistryTests(unittest.TestCase):
    def test_latest_registry_has_43_unique_permanent_keys(self):
        rows = registry_records()
        self.assertEqual(len(rows), 43)
        self.assertEqual(len({row["registry_number"] for row in rows}), 43)
        by_id = {row["registry_number"]: row for row in rows}
        self.assertEqual(by_id[19]["instrument_name"], "Anemometer")
        self.assertEqual(by_id[20]["instrument_name"], "Wind Vane")
        self.assertEqual(by_id[59]["element_ids"], ["10"])
        self.assertEqual(by_id[68]["element_ids"], ["184"])
        self.assertEqual(by_id[18]["parameters_taken"], "AWS_Visibility")
        self.assertEqual(by_id[21]["parameters_taken"].split("|")[-1], "AWS_Dew Point")

    def test_registry_is_immutable_but_omitting_it_preserves_identity(self):
        check_registry_change({"registry_number":59},59)
        check_registry_change({"registry_number":59},None)
        with self.assertRaises(HTTPException) as error:
            check_registry_change({"registry_number":59},68)
        self.assertEqual(error.exception.status_code,409)

    def test_duplicate_names_are_distinct_by_registry_during_import(self):
        rows, merged = main.parse_instrument_import("Registry Number,Instrument,Parameters,Category,Station categories,Elements\n59,Hygrograph,Humidity_Hygro,Sensing,Principal Station,10\n68,Hygrograph,Relative humidity,Sensing,Principal Station,184\n")
        self.assertEqual(merged,0)
        self.assertEqual([row.registry_number for row in rows],[59,68])

    def test_duplicate_registry_with_different_names_is_rejected(self):
        with self.assertRaises(HTTPException):
            main.parse_instrument_import("Registry Number,Instrument,Parameters,Category,Station categories,Elements\n59,Hygrograph,Humidity,Sensing,Principal Station,10\n59,Another instrument,Humidity,Sensing,Principal Station,10\n")

    def test_shared_element_keeps_both_instrument_names(self):
        cursor = MagicMock()
        cursor.fetchall.side_effect = [[
            {"instrument_name":"Soil probe","element_ids":'["2112"]',"parameters_taken":"AWS_Temperature at 10 Cm"},
            {"instrument_name":"Soil thermometer","element_ids":'["2112"]',"parameters_taken":"AWS_Soil temperature"}], [], [], []]
        inventory = load_inventory(cursor,{"A"})
        self.assertEqual(inventory["catalog_errors"],[])
        self.assertIn("AWS_Temperature at 10 Cm",inventory["element_names"]["2112"])
        self.assertIn("AWS_Soil temperature",inventory["element_names"]["2112"])

    @patch("backend.main.ensure_application_tables")
    @patch("backend.main.audit_snapshot",return_value={"registry_number":59})
    @patch("backend.main.get_connection")
    def test_api_rejects_registry_change_even_for_admin(self,connect,_snapshot,_ensure):
        main.app.dependency_overrides[main.require_it]=lambda:{"user_id":1,"username":"admin","department":"Admin"}
        connect.return_value.cursor.return_value.fetchall.return_value=[("Principal Station",)]
        try:
            response=TestClient(main.app).put("/instruments/5081",json=dict(registry_number=68,
                instrument_name="Renamed",parameters_taken="Humidity",category="Sensing",station_categories=["Principal Station"]))
            self.assertEqual(response.status_code,409,response.text)
            self.assertIn("permanent",response.json()["detail"])
        finally:
            main.app.dependency_overrides.clear()

    @patch("backend.main.record_entity_edit")
    @patch("backend.main.ensure_application_tables")
    @patch("backend.main.audit_snapshot",return_value={"registry_number":59,"instrument_id":5081})
    @patch("backend.main.get_connection")
    def test_name_edit_keeps_same_registry_and_legacy_link(self,connect,_snapshot,_ensure,history):
        main.app.dependency_overrides[main.require_it]=lambda:{"user_id":1,"username":"admin","department":"Admin","edit_reason":"Rename instrument"}
        cursor=connect.return_value.cursor.return_value
        cursor.fetchall.return_value=[("Principal Station",)]
        try:
            response=TestClient(main.app).put("/instruments/5081",json=dict(registry_number=59,
                instrument_name="Renamed Hygrograph",parameters_taken="Humidity",category="Sensing",station_categories=["Principal Station"]))
            self.assertEqual(response.status_code,200,response.text)
            updates=[call for call in cursor.execute.call_args_list if "UPDATE instruments" in call.args[0]]
            self.assertEqual(updates[0].args[1][0],"Renamed Hygrograph")
            self.assertEqual(updates[0].args[1][1],59)
            self.assertEqual(updates[0].args[1][-1],5081)
            history.assert_called_once()
            connect.return_value.commit.assert_called_once()
        finally:
            main.app.dependency_overrides.clear()


if __name__ == "__main__":
    unittest.main()
