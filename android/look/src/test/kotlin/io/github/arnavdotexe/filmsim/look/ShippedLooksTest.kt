package io.github.arnavdotexe.filmsim.look

import java.io.File
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertTrue

/** The looks bundled in the app (android/app/src/main/assets/looks) and the library that loads them. */
class ShippedLooksTest {
    private val dir = File(checkNotNull(System.getProperty("filmsim.shippedLooks")) { "filmsim.shippedLooks not set" })

    private fun sources(): List<LookSource> =
        dir.listFiles { f -> f.name.endsWith(".fsim.json") }.orEmpty().map { LookSource(it.name, it.readText()) }

    @Test
    fun everyShippedLookLoadsWithItsOwnNameAndLicence() {
        val library = LookLibrary.load(sources())
        assertTrue(library.looks.size >= 40, "only ${library.looks.size} looks")
        for ((file, look) in library.byFile) {
            assertEquals(LookLibrary.fileName(look.name), file)
            assertEquals("Emulsion", look.author)
            assertEquals("GPL-3.0-or-later", look.license)
            assertTrue(look.description.isNotBlank(), "${look.name} has no description")
            assertEquals(33, look.lut.size)
        }
    }

    @Test
    fun libraryIsSortedByNameAndRejectsDuplicatesAndBadFiles() {
        val warm = javaClass.getResource("/conformance/warm_fade.fsim.json")!!.readText()
        val other = warm.replace("Conformance Warm Fade", "Another Look")
        val library = LookLibrary.load(listOf(LookSource("b.fsim.json", warm), LookSource("a.fsim.json", other)))
        assertEquals(listOf("Another Look", "Conformance Warm Fade"), library.looks.map { it.name })
        assertEquals("Conformance Warm Fade", library.named("Conformance Warm Fade")?.name)
        assertEquals(null, library.named("Missing"))
        assertFailsWith<FsimFormatException> {
            LookLibrary.load(listOf(LookSource("a.fsim.json", warm), LookSource("b.fsim.json", warm)))
        }
        val error = assertFailsWith<FsimFormatException> { LookLibrary.load(listOf(LookSource("bad.fsim.json", "{}"))) }
        assertTrue(error.message!!.startsWith("bad.fsim.json: "), error.message)
    }

    @Test
    fun fileNamesAreLowercaseSlugs() {
        assertEquals("late-august.fsim.json", LookLibrary.fileName("Late August"))
        assertEquals("fog-mono.fsim.json", LookLibrary.fileName("  Fog  Mono! "))
    }
}
