package io.github.arnavdotexe.filmsim.look

/** A look file that breaks the `.fsim.json` v1 rules. */
class FsimFormatException(message: String) : IllegalArgumentException(message)

/** One setting of a spatial section, in Lightroom slider units. */
data class SettingSpec(val default: Double, val min: Double, val max: Double, val wholeNumber: Boolean = false)

/** The spatial sections of a look and their settings (colorlab's `fsim.SETTINGS`). */
enum class Section(val key: String, val settings: Map<String, SettingSpec>) {
    LOCAL(
        "local",
        linkedMapOf(
            "luminance_noise_reduction" to SettingSpec(0.0, 0.0, 100.0),
            "color_noise_reduction" to SettingSpec(0.0, 0.0, 100.0),
            "highlights" to SettingSpec(0.0, -100.0, 100.0),
            "shadows" to SettingSpec(0.0, -100.0, 100.0),
            "dehaze" to SettingSpec(0.0, -100.0, 100.0),
            "texture" to SettingSpec(0.0, -100.0, 100.0),
            "clarity" to SettingSpec(0.0, -100.0, 100.0),
            "sharpen_amount" to SettingSpec(0.0, 0.0, 150.0),
            "sharpen_radius" to SettingSpec(1.0, 0.5, 3.0),
            "sharpen_detail" to SettingSpec(25.0, 0.0, 100.0),
            "sharpen_masking" to SettingSpec(0.0, 0.0, 100.0),
        ),
    ),
    VIGNETTE(
        "vignette",
        linkedMapOf(
            "amount" to SettingSpec(0.0, -100.0, 100.0),
            "midpoint" to SettingSpec(50.0, 0.0, 100.0),
            "feather" to SettingSpec(50.0, 0.0, 100.0),
            "roundness" to SettingSpec(0.0, -100.0, 100.0),
            "style" to SettingSpec(1.0, 1.0, 3.0, wholeNumber = true),
            "highlights" to SettingSpec(0.0, 0.0, 100.0),
        ),
    ),
    GRAIN(
        "grain",
        linkedMapOf(
            "amount" to SettingSpec(0.0, 0.0, 100.0),
            "size" to SettingSpec(25.0, 0.0, 100.0),
            "roughness" to SettingSpec(50.0, 0.0, 100.0),
        ),
    ),
}

/**
 * A look: its global colour as [lut] and its spatial settings per section. The section maps
 * hold only the settings that differ from the default; [setting] fills in the rest.
 */
class Look(
    val name: String,
    val lut: Lut3D,
    val local: Map<String, Double> = emptyMap(),
    val vignette: Map<String, Double> = emptyMap(),
    val grain: Map<String, Double> = emptyMap(),
    val description: String = "",
    val author: String = "",
    val license: String = "",
) {
    fun section(section: Section): Map<String, Double> = when (section) {
        Section.LOCAL -> local
        Section.VIGNETTE -> vignette
        Section.GRAIN -> grain
    }

    /** The value of [name] in [section], or its default. */
    fun setting(section: Section, name: String): Double {
        val spec = requireNotNull(section.settings[name]) { "${section.key} has no setting $name" }
        return section(section)[name] ?: spec.default
    }
}
