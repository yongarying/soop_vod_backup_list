import unittest
from datetime import date
from unittest.mock import patch

from app import build_public_snapshot, build_settings, classify_vod, normalize_url


def sample_vod(
    *,
    title_no: int,
    reg_date: str,
    pure_views: int,
    display_views=None,
    comment_count: int = 0,
):
    return {
        "title_no": title_no,
        "title_name": "test vod",
        "reg_date": reg_date,
        "count": {
            "read_cnt": pure_views if display_views is None else display_views,
            "vod_read_cnt": pure_views,
            "comment_cnt": comment_count,
            "like_cnt": 0,
        },
        "ucc": {
            "total_file_duration": 3600000,
            "thumb": "//videoimg.sooplive.com/test.jpg",
            "file_type": "REVIEW",
            "grade": 0,
        },
    }


class PolicyClassificationTests(unittest.TestCase):
    def test_display_views_matches_pure_views_before_policy_change(self):
        vod = sample_vod(
            title_no=10,
            reg_date="2025-01-14 11:34:59",
            pure_views=120,
            display_views=10120,
        )
        result = classify_vod(vod, "best", date(2026, 6, 1), {"ids": set(), "titles": set()})
        self.assertEqual(result["pure_views"], 10120)
        self.assertEqual(result["display_views"], 10120)
        self.assertEqual(result["estimated_live_views"], 0)
        self.assertFalse(result["merged_view_count_applies"])

    def test_view_breakdown_uses_pure_and_display_fields(self):
        vod = sample_vod(
            title_no=11,
            reg_date="2025-01-14 12:00:00",
            pure_views=120,
            display_views=10120,
        )
        result = classify_vod(vod, "best", date(2026, 6, 1), {"ids": set(), "titles": set()})
        self.assertEqual(result["pure_views"], 120)
        self.assertEqual(result["display_views"], 10120)
        self.assertEqual(result["estimated_live_views"], 10000)
        self.assertTrue(result["merged_view_count_applies"])

    def test_general_50_view_vod_can_delete_on_policy_day(self):
        vod = sample_vod(title_no=1, reg_date="2024-06-01 00:00:00", pure_views=50)
        result = classify_vod(vod, "general", date(2026, 6, 1), {"ids": set(), "titles": set()})
        self.assertTrue(result["delete_on_policy_day"])
        self.assertEqual(result["future_expiry_date"], "2025-06-01")

    def test_best_streamer_1000_views_exactly_is_not_permanent(self):
        vod = sample_vod(title_no=3, reg_date="2024-05-30 00:00:00", pure_views=1000)
        result = classify_vod(vod, "best", date(2026, 6, 1), {"ids": set(), "titles": set()})
        self.assertTrue(result["delete_on_policy_day"])
        self.assertEqual(result["future_expiry_date"], "2026-05-30")
        self.assertFalse(result["views_1000_plus"])
        self.assertTrue(result["views_900_plus"])

    def test_best_streamer_more_than_1000_views_is_permanent(self):
        vod = sample_vod(title_no=5, reg_date="2024-05-30 00:00:00", pure_views=1001)
        result = classify_vod(vod, "best", date(2026, 6, 1), {"ids": set(), "titles": set()})
        self.assertTrue(result["future_permanent"])
        self.assertEqual(result["future_reason"], "best_views_over_1000")

    def test_best_streamer_basic_two_years_can_survive_policy_day(self):
        vod = sample_vod(title_no=7, reg_date="2025-01-01 00:00:00", pure_views=300)
        result = classify_vod(vod, "best", date(2026, 6, 1), {"ids": set(), "titles": set()})
        self.assertFalse(result["delete_on_policy_day"])
        self.assertEqual(result["future_expiry_date"], "2027-01-01")
        self.assertEqual(result["future_reason"], "best_basic_2_years")

    def test_partner_streamer_remains_permanent(self):
        vod = sample_vod(title_no=4, reg_date="2020-01-01 00:00:00", pure_views=0)
        result = classify_vod(vod, "partner", date(2026, 6, 1), {"ids": set(), "titles": set()})
        self.assertTrue(result["future_permanent"])
        self.assertFalse(result["needs_pre_policy_support"])

    def test_comment_api_auto_confirmation_keeps_permanent(self):
        vod = sample_vod(title_no=6, reg_date="2024-01-01 00:00:00", pure_views=0)
        result = classify_vod(
            vod,
            "general",
            date(2026, 6, 1),
            {"ids": set(), "titles": set()},
            {
                "supported": True,
                "kind": "starballoon",
                "amount": 10,
                "comment_no": "12345",
                "checked_at": "2026-04-20T12:00:00+00:00",
            },
        )
        self.assertTrue(result["future_permanent"])
        self.assertTrue(result["auto_support_confirmed"])
        self.assertEqual(result["support_confirmation_mode"], "auto")


class SettingsTests(unittest.TestCase):
    def test_display_name_builds_default_page_labels(self):
        with patch.dict(
            "os.environ",
            {
                "SOOP_STREAMER_ID": "tester123",
                "SOOP_DISPLAY_NAME": "테스트BJ",
            },
            clear=True,
        ):
            settings = build_settings()

        self.assertEqual(settings.display_name, "테스트BJ")
        self.assertEqual(settings.page_title, "테스트BJ 다시보기 백업")
        self.assertEqual(settings.page_heading, "테스트BJ 다시보기 살리기 운동")

    def test_custom_page_labels_can_override_defaults(self):
        with patch.dict(
            "os.environ",
            {
                "SOOP_DISPLAY_NAME": "테스트BJ",
                "SOOP_PAGE_TITLE": "{display_name} VOD 현황판",
                "SOOP_PAGE_HEADING": "{display_name} 보관 대시보드",
            },
            clear=True,
        ):
            settings = build_settings()

        self.assertEqual(settings.page_title, "테스트BJ VOD 현황판")
        self.assertEqual(settings.page_heading, "테스트BJ 보관 대시보드")


class SecurityTests(unittest.TestCase):
    def test_normalize_url_rejects_non_http_schemes(self):
        self.assertEqual(normalize_url("javascript:alert(1)"), "")
        self.assertEqual(normalize_url("data:text/html,hi"), "")
        self.assertEqual(normalize_url("//videoimg.sooplive.com/test.jpg"), "https://videoimg.sooplive.com/test.jpg")

    def test_build_public_snapshot_strips_internal_fields(self):
        snapshot = {
            "streamer_id": "kyaang123",
            "page_title": "테스트 다시보기 백업",
            "page_heading": "테스트 다시보기 살리기 운동",
            "policy_date": "2026-06-01",
            "generated_at": "2026-04-20T12:00:00+00:00",
            "summary": {
                "total": 1,
                "policy_day_delete": 0,
                "soon_after_policy": 0,
                "other_count": 0,
                "views_900_plus": 1,
                "views_1000_plus": 1,
                "future_permanent": 1,
                "confirmed": 0,
                "api_auto_delete": 1,
            },
            "vods": [
                {
                    "title_no": "1",
                    "title_name": "test vod",
                    "player_url": "https://vod.sooplive.com/player/1",
                    "thumbnail_url": "https://videoimg.sooplive.com/test.jpg",
                    "uploaded_at": "2026-04-20T12:00:00",
                    "duration_label": "1:00:00",
                    "display_views": 1200,
                    "pure_views": 1001,
                    "estimated_live_views": 199,
                    "merged_view_count_applies": True,
                    "comment_count": 3,
                    "future_permanent": True,
                    "future_expiry_date": None,
                    "future_reason": "best_views_over_1000",
                    "delete_on_policy_day": False,
                    "urgency": "safe",
                    "support_confirmed": False,
                    "support_confirmation_mode": "none",
                    "auto_support_confirmed": False,
                    "auto_support_kind": None,
                    "auto_support_amount": 0,
                    "auto_support_user_nick": "",
                    "auto_support_reg_date": None,
                    "views_900_plus": True,
                    "views_1000_plus": True,
                    "auto_support_user_id": "secret",
                    "raw_grade": 0,
                }
            ],
        }

        public_snapshot = build_public_snapshot(snapshot)

        self.assertEqual(public_snapshot["summary"]["total"], 1)
        self.assertNotIn("streamer_id", public_snapshot)
        self.assertNotIn("api_auto_delete", public_snapshot["summary"])
        self.assertNotIn("auto_support_user_id", public_snapshot["vods"][0])
        self.assertNotIn("raw_grade", public_snapshot["vods"][0])


if __name__ == "__main__":
    unittest.main()
