package io.github.arnavdotexe.filmsim.look

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.double
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonPrimitive
import java.io.File
import kotlin.math.abs
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertTrue

/** The Kotlin reader and LUT lookup against colorlab's fixtures (make_conformance.py). */
class FsimConformanceTest {
    private fun resource(path: String): String =
        checkNotNull(javaClass.getResource("/conformance/$path")) { "missing fixture $path" }.readText()

    @Test
    fun readsTheReferenceLookWithItsSettingsAndDefaults() {
        val look = FsimReader.parse(resource("warm_fade.fsim.json"))
        assertEquals("Conformance Warm Fade", look.name)
        assertEquals("Emulsion", look.author)
        assertEquals("GPL-3.0-or-later", look.license)
        assertEquals(9, look.lut.size)
        assertEquals(10.0, look.setting(Section.LOCAL, "clarity"))
        assertEquals(-20.0, look.setting(Section.VIGNETTE, "amount"))
        assertEquals(50.0, look.setting(Section.VIGNETTE, "midpoint")) // left out: the default
        assertEquals(12.0, look.setting(Section.GRAIN, "amount"))
        assertEquals(30.0, look.setting(Section.GRAIN, "size"))
        assertEquals(mapOf("clarity" to 10.0), look.local) // only what differs from the defaults
    }

    @Test
    fun rejectsEveryFileColorlabRejects() {
        val dir = File(checkNotNull(javaClass.getResource("/conformance/invalid")).toURI())
        val files = dir.listFiles { f -> f.name.endsWith(".fsim.json") }.orEmpty().sortedBy { it.name }
        assertEquals(21, files.size)
        for (file in files) {
            assertFailsWith<FsimFormatException>(file.name) { FsimReader.parse(file.readText()) }
        }
    }

    @Test
    fun rejectsTextOverTheSizeLimit() {
        val text = resource("warm_fade.fsim.json")
        val padded = text.trimEnd().dropLast(1) + ", \"description\": \"" + "x".repeat(FsimReader.MAX_TEXT_BYTES) + "\"}"
        assertFailsWith<FsimFormatException> { FsimReader.parse(padded) }
    }

    @Test
    fun lookupMatchesColorlabTetrahedralInterpolation() {
        val look = FsimReader.parse(resource("warm_fade.fsim.json"))
        val doc = Json.parseToJsonElement(resource("warm_fade_samples.json")) as JsonObject
        val inputs = doc.getValue("inputs").jsonArray
        val outputs = doc.getValue("outputs").jsonArray
        assertEquals(248, inputs.size)
        val out = FloatArray(3)
        var worst = 0.0
        for (i in inputs.indices) {
            val rgb = inputs[i].jsonArray.map { it.jsonPrimitive.double.toFloat() }
            look.lut.lookup(rgb[0], rgb[1], rgb[2], out)
            val want = outputs[i].jsonArray.map { it.jsonPrimitive.double }
            for (c in 0..2) worst = maxOf(worst, abs(out[c] - want[c]))
        }
        assertTrue(worst < 2e-6, "worst channel difference $worst")
    }

    @Test
    fun identityLutPassesColoursThroughAndClamps() {
        val lut = Lut3D.identity(17)
        val out = FloatArray(3)
        lut.lookup(0.25f, 0.5f, 0.8125f, out)
        assertEquals(listOf(0.25f, 0.5f, 0.8125f), out.toList().map { (it * 1e5f).toInt() / 1e5f })
        lut.lookup(-1f, 2f, 0.5f, out)
        assertEquals(listOf(0f, 1f, 0.5f), out.toList().map { (it * 1e5f).toInt() / 1e5f })
    }
}
