import pytest

from refund_service.masking import mask_mobile, mask_upi_handle


@pytest.mark.parametrize(
    "handle,masked",
    [
        ("ann@upi", "an*@upi"),
        ("ab@upi", "**@upi"),
        ("a@upi", "*@upi"),
        ("rahul@okbank", "ra***@okbank"),
    ],
)
def test_mask_upi_handle(handle, masked):
    assert mask_upi_handle(handle) == masked


def test_mask_never_exposes_local_part_beyond_two_chars():
    masked = mask_upi_handle("rahul123@okbank")
    assert masked.startswith("ra") and "hul" not in masked
    assert len(masked.split("@")[0]) == len("rahul123")


def test_invalid_handle_rejected():
    with pytest.raises(ValueError):
        mask_upi_handle("nohandle")


def test_mask_mobile():
    assert mask_mobile("9876543210") == "XXXXXX3210"
