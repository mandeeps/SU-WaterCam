"""tools/mdot_subband.py: the sub-band in an AT+CHM? answer, and what to change."""
from tools.mdot_subband import GATEWAY_SUB_BAND, changes_needed, data_rate, sub_band_from_mask


def test_our_gateway_mask_is_sub_band_1():
    # 005 after a join, 2026-10-14
    assert sub_band_from_mask("000100000000000000FF") == 1 == GATEWAY_SUB_BAND


def test_other_sub_bands():
    assert sub_band_from_mask("0002000000000000FF00") == 2       # channels 8-15
    assert sub_band_from_mask("00040000000000FF0000") == 3       # channels 16-23
    assert sub_band_from_mask("00800000FF0000000000") == 6       # channels 40-47
    assert sub_band_from_mask("0080FF00000000000000") == 8       # channels 56-63


def test_masks_that_are_not_one_sub_band():
    assert sub_band_from_mask("00FFFFFFFFFFFFFFFFFF") is None    # all channels (sub-band 0)
    assert sub_band_from_mask("0001000000000000000F") is None    # part of one
    assert sub_band_from_mask("00030000000000FFFFFF") is None    # two
    assert sub_band_from_mask("") is None
    assert sub_band_from_mask("not a mask") is None


def test_sub_band_search_is_turned_off():
    # 006 at its site, 2026-10-24: sub-band drifted to 3 with join retries 2
    assert changes_needed("3", "2", 1) == ["AT+FSB=1", "AT+JR=0"]
    assert changes_needed("1", "2", 1) == ["AT+JR=0"]
    assert changes_needed("8", "0", 1) == ["AT+FSB=1"]
    assert changes_needed("1", "0", 1) == []


def test_unreadable_values_are_set():
    assert changes_needed("", "", 1) == ["AT+FSB=1", "AT+JR=0"]


def test_adr_and_data_rate_left_alone_by_default():
    assert changes_needed("1", "0", 1, "1, 64, 32", "DR2 - SF8BW125") == []


def test_fixed_data_rate_turns_adr_off():
    # 006 at its site, 2026-10-25: ADR moved it from DR1 to DR2 at -11 dB SNR
    assert changes_needed("1", "0", 1, "1, 64, 32", "DR2 - SF8BW125", fixed_dr=1) == ["AT+ADR=0", "AT+TXDR=1"]
    assert changes_needed("1", "0", 1, "0, 64, 32", "DR2 - SF8BW125", fixed_dr=1) == ["AT+TXDR=1"]
    assert changes_needed("1", "0", 1, "0, 64, 32", "DR1 - SF9BW125", fixed_dr=1) == []
    assert changes_needed("3", "2", 1, "", "", fixed_dr=0) == ["AT+FSB=1", "AT+JR=0", "AT+ADR=0", "AT+TXDR=0"]


def test_adr_back_on():
    assert changes_needed("1", "0", 1, "0, 64, 32", "DR1 - SF9BW125", adr_on=True) == ["AT+ADR=1"]
    assert changes_needed("1", "0", 1, "1, 64, 32", "DR1 - SF9BW125", adr_on=True) == []


def test_data_rate_answer():
    assert data_rate("DR1 - SF9BW125") == 1
    assert data_rate("DR0 - SF10BW125") == 0
    assert data_rate("") is None and data_rate(None) is None
