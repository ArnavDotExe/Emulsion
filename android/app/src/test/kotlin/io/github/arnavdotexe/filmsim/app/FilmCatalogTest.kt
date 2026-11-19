package io.github.arnavdotexe.filmsim.app

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertNull

/** The films tab's catalogue: houses, folded grain variants, order and hints. */
class FilmCatalogTest {
    private val looks = listOf(
        "Portrait 400" to "The classic portrait colour negative, 400 speed. Neutral, natural contrast.",
        "Portrait 400 Grain" to "The classic portrait colour negative, 400 speed. With grain. Neutral, grain.",
        "Analog 1" to "Everyday analog colour. Cool, punchy contrast.",
        "Neg Standard Holiday" to "Digital film recipe on a standard negative base: warm holidays. Neutral.",
        "Slide Clean Blue" to "Digital film recipe on a standard slide base: clean blues. Cool.",
        "Chrome 64" to "Classic 1960s slide film, 64 speed: dense reds, deep blues. Neutral.",
        "Fine Mono 100" to "Fine-grain black and white, 100 speed. Black and white, natural contrast.",
        "Cine 500T" to "Tungsten cinema film, 500 speed, with its red halation glow. Warm.",
        "Instant Muted" to "Muted instant film. Cool, punchy contrast.",
        "Colour 1940s" to "Early European colour film of the 1940s. Cool.",
        "Retro Fade 1" to "Faded retro print. Neutral.",
        "Infrared 16" to "Colour infrared film: foliage turns red. Cool.",
        "Sepia Recipe" to "Digital film recipe: vintage sepia. Warm.",
        "Vivid" to "Bright, saturated colour. Neutral, punchy contrast.",
    )

    @Test
    fun eachLookBelongsToTheHouseWhoseFilmItEvokes() {
        val houses = FilmCatalog.build(looks).associate { it.name to it.house }
        assertEquals(House.GOLDBOX, houses["Portrait 400"])
        assertEquals(House.GOLDBOX, houses["Chrome 64"])
        assertEquals(House.GOLDBOX, houses["Cine 500T"])
        assertEquals(House.GOLDBOX, houses["Infrared 16"])
        assertEquals(House.GREENLEAF, houses["Neg Standard Holiday"])
        assertEquals(House.GREENLEAF, houses["Slide Clean Blue"])
        assertEquals(House.ARGENTUM, houses["Fine Mono 100"])
        assertEquals(House.SNAPSHOT, houses["Instant Muted"])
        assertEquals(House.OLD_EUROPE, houses["Colour 1940s"])
        assertEquals(House.RECIPES, houses["Sepia Recipe"])
        assertEquals(House.STUDIO, houses["Analog 1"])
        assertEquals(House.STUDIO, houses["Retro Fade 1"])
        assertEquals(House.STUDIO, houses["Vivid"])
    }

    @Test
    fun housesAreNamedByTheirLookNotByAMakersTrademark() {
        val shown = House.entries.joinToString(" ") { it.title + " " + it.tagline }.lowercase()
        for (mark in listOf("fuji", "kodak", "agfa", "ilford", "polaroid", "portra", "provia", "velvia"))
            assertEquals(false, mark in shown, mark)
        assertEquals(listOf("Goldbox", "Greenleaf", "Argentum", "Snapshot", "Old Europe", "Studio", "Recipes"), House.entries.map { it.title })
    }

    @Test
    fun grainVariantsFoldIntoTheirFilm() {
        val films = FilmCatalog.build(looks)
        assertEquals(looks.size - 1, films.size)
        val portrait = films.single { it.name == "Portrait 400" }
        assertEquals("Portrait 400", portrait.lookName)
        assertEquals("Portrait 400 Grain", portrait.grainLookName)
        assertNull(films.single { it.name == "Vivid" }.grainLookName)
        assertEquals(portrait, FilmCatalog.filmOf(films, "Portrait 400 Grain"))
        assertNull(FilmCatalog.filmOf(films, "Missing"))
    }

    @Test
    fun filmsAreOrderedByHouseThenName() {
        val films = FilmCatalog.build(looks)
        assertEquals(films.sortedWith(compareBy({ it.house.ordinal }, { it.name.lowercase() })), films)
        assertEquals(listOf("Chrome 64", "Cine 500T", "Infrared 16", "Portrait 400"), films.filter { it.house == House.GOLDBOX }.map { it.name })
    }

    @Test
    fun theHintIsTheDescriptionsFirstSentence() {
        val films = FilmCatalog.build(looks)
        assertEquals("The classic portrait colour negative, 400 speed", films.single { it.name == "Portrait 400" }.hint)
        assertEquals("Bright, saturated colour", films.single { it.name == "Vivid" }.hint)
    }

    @Test
    fun exposureChipShowsAutoTheCompensationOrManual() {
        assertEquals("AUTO", CameraControls.exposureChip(0, 1f / 6, manual = false))
        assertEquals("+0.7", CameraControls.exposureChip(4, 1f / 6, manual = false))
        assertEquals("M", CameraControls.exposureChip(3, 1f / 6, manual = true))
    }
}
