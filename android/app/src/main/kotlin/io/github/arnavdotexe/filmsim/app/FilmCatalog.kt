package io.github.arnavdotexe.filmsim.app

/**
 * The houses the films tab groups the looks by, in display order. Each is named for the look of
 * the film it evokes (a yellow box, a green label, silver halide...), never by a maker's trademark.
 */
enum class House(val title: String, val tagline: String) {
    GOLDBOX("Goldbox", "Warm golden classics: portrait negative, rich slide, cinema and infrared"),
    GREENLEAF("Greenleaf", "Airy green-label film: pastel and pro negatives, clean slide"),
    ARGENTUM("Argentum", "Silver black and white"),
    SNAPSHOT("Snapshot", "Instant prints, soft and muted"),
    OLD_EUROPE("Old Europe", "Early colour film from the 1940s"),
    STUDIO("Studio", "Everyday analog colour and faded retro prints"),
    RECIPES("Recipes", "Digital film recipes for street, portrait and landscape"),
}

/** The demo images a film is previewed on in the films tab (bundled, drawn by tools/demo_images). */
enum class DemoScene(val title: String, val assetPath: String) {
    LANDSCAPE("Landscape", "demo/landscape.png"),
    PORTRAIT("Portrait", "demo/portrait.png"),
    STILL_LIFE("Still life", "demo/still_life.png"),
}

/**
 * One card in the films tab: a look, and its "… Grain" twin when there is one (the same colour
 * with grain added, so the two share a card).
 */
data class Film(
    val name: String,
    val lookName: String,
    val grainLookName: String?,
    val house: House,
    val hint: String,
)

object FilmCatalog {
    private const val GRAIN_SUFFIX = " Grain"

    private val GOLDBOX_FILMS = listOf("Portrait 400", "Chrome 64", "Slide 200", "Infrared 16", "Cine 500T")

    /** The house a look belongs to, from its name (a "… Grain" twin sits with its film). */
    fun houseOf(name: String): House {
        val base = name.removeSuffix(GRAIN_SUFFIX)
        return when {
            GOLDBOX_FILMS.any { base.startsWith(it) } -> House.GOLDBOX
            base.startsWith("Pro Pastel") || base.startsWith("Neg ") || base.startsWith("Slide Clean") -> House.GREENLEAF
            base.endsWith(" Recipe") -> House.RECIPES
            base.startsWith("Fine Mono") -> House.ARGENTUM
            base.startsWith("Instant") -> House.SNAPSHOT
            base.startsWith("Colour 1940s") -> House.OLD_EUROPE
            else -> House.STUDIO
        }
    }

    /** The cards for looks given as (name, description): grain twins folded, ordered by house then name. */
    fun build(looks: List<Pair<String, String>>): List<Film> {
        val names = looks.map { it.first }.toSet()
        return looks
            .filterNot { (name, _) -> name.endsWith(GRAIN_SUFFIX) && name.removeSuffix(GRAIN_SUFFIX) in names }
            .map { (name, description) ->
                val grain = (name + GRAIN_SUFFIX).takeIf { it in names }
                Film(name, name, grain, houseOf(name), description.substringBefore('.').trim())
            }
            .sortedWith(compareBy({ it.house.ordinal }, { it.name.lowercase() }))
    }

    /** The card that holds the look named [lookName] (itself or its grain twin). */
    fun filmOf(films: List<Film>, lookName: String): Film? =
        films.firstOrNull { it.lookName == lookName || it.grainLookName == lookName }
}
