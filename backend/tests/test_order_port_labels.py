from app.services.text_encoding import restore_utf8_cjk_label


def test_restores_utf8_chinese_bytes_persisted_as_latin1() -> None:
    corrupted = "\xe8\xbe\x93\xe9\x80\x81\xe7\xba\xbf\xe5\x87\xba\xe5\x85\xa5\xe5\x8f\xa3 4"

    assert restore_utf8_cjk_label(corrupted) == "输送线出入口 4"


def test_keeps_correct_unicode_and_unrelated_latin_text_unchanged() -> None:
    assert restore_utf8_cjk_label("输送线出入口 4") == "输送线出入口 4"
    assert restore_utf8_cjk_label("Café Port 4") == "Café Port 4"
