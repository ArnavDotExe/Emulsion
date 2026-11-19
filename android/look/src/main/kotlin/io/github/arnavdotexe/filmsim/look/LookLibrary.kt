package io.github.arnavdotexe.filmsim.look

/** One look file's name and text, from the app's assets or the user's imports. */
data class LookSource(val fileName: String, val text: String)

/** A set of looks with unique names, sorted by name. */
class LookLibrary private constructor(val byFile: Map<String, Look>) {
    val looks: List<Look> = byFile.values.sortedBy { it.name.lowercase() }
    private val byName = looks.associateBy { it.name }

    fun named(name: String): Look? = byName[name]

    companion object {
        /** Parse every source; a bad file or a repeated name raises [FsimFormatException] naming the file. */
        fun load(sources: List<LookSource>): LookLibrary {
            val byFile = linkedMapOf<String, Look>()
            val seen = hashMapOf<String, String>()
            for (source in sources.sortedBy { it.fileName }) {
                val look = try {
                    FsimReader.parse(source.text)
                } catch (e: FsimFormatException) {
                    throw FsimFormatException("${source.fileName}: ${e.message}")
                }
                seen.put(look.name, source.fileName)?.let {
                    throw FsimFormatException("${source.fileName}: the name '${look.name}' is already used by $it")
                }
                byFile[source.fileName] = look
            }
            return LookLibrary(byFile)
        }

        /** The file name a look is shipped under: its name as a lowercase slug. */
        fun fileName(name: String): String =
            name.lowercase().replace(Regex("[^a-z0-9]+"), "-").trim('-') + ".fsim.json"
    }
}
