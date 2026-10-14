"""tools/mdot_subband.py: the sub-band in an AT+CHM? answer."""
from tools.mdot_subband import GATEWAY_SUB_BAND, sub_band_from_mask


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
