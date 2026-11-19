package io.github.arnavdotexe.filmsim.app

/** The kinds of film the films tab groups the looks by, in display order. */
enum class FilmKind(val label: String) {
    NEGATIVE("Colour negative"),
    SLIDE("Slide"),
    MONO("Black & white"),
    CINE("Cinema"),
    INSTANT("Instant"),
    VINTAGE("Vintage"),
    CREATIVE("Recipes & creative"),
}

/**
 * One card in the films tab: a look, and its "… Grain" twin when there is one (the same colour
 * with grain added, so the two share a card).
 */
data class Film(
    val name: String,
    val lookName: String,
    val grainLookName: String?,
    val kind: FilmKind,
    val hint: String,
)

object FilmCatalog {
    private const val GRAIN_SUFFIX = " Grain"

    /** The kind of film a look evokes, from its description (and name). */
    fun kindOf(name: String, description: String): FilmKind {
        val d = description.lowercase()
        val n = name.lowercase()
        return when {
            "black and white" in d -> FilmKind.MONO
            "slide film" in d || "slide base" in d -> FilmKind.SLIDE
            "colour negative" in d || "negative base" in d || "everyday analog" in d -> FilmKind.NEGATIVE
            "cinema" in d -> FilmKind.CINE
            "instant" in d -> FilmKind.INSTANT
            "1940s" in d || "retro" in d || "infrared" in d || "infrared" in n -> FilmKind.VINTAGE
            else -> FilmKind.CREATIVE
        }
    }

    /** The cards for looks given as (name, description): grain twins folded, ordered by kind then name. */
    fun build(looks: List<Pair<String, String>>): List<Film> {
        val names = looks.map { it.first }.toSet()
        return looks
            .filterNot { (name, _) -> name.endsWith(GRAIN_SUFFIX) && name.removeSuffix(GRAIN_SUFFIX) in names }
            .map { (name, description) ->
                val grain = (name + GRAIN_SUFFIX).takeIf { it in names }
                Film(name, name, grain, kindOf(name, description), description.substringBefore('.').trim())
            }
            .sortedWith(compareBy({ it.kind.ordinal }, { it.name.lowercase() }))
    }

    /** The card that holds the look named [lookName] (itself or its grain twin). */
    fun filmOf(films: List<Film>, lookName: String): Film? =
        films.firstOrNull { it.lookName == lookName || it.grainLookName == lookName }
}
