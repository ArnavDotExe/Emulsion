"""Exception types. Every failure names the file and/or setting involved."""


class ColorlabError(Exception):
    """Base class for all colorlab errors."""


class PresetFormatError(ColorlabError):
    """A Lightroom preset (.xmp / .dng) is malformed or unsupported."""


class TableDecodeError(ColorlabError):
    """An Adobe RGBTable payload could not be decoded."""


class LutFormatError(ColorlabError):
    """A LUT (.cube / Hald image / table array) is malformed or unsupported."""


class ParameterError(ColorlabError):
    """A processing parameter is outside its valid range."""
