package io.github.arnavdotexe.filmsim.look

import kotlin.math.roundToInt
import kotlin.test.Test
import kotlin.test.assertContentEquals
import kotlin.test.assertEquals

/** Applying a look's LUT to 8-bit ARGB pixels, as the app does to each captured frame. */
class ArgbTest {
    private val look = FsimReader.parse(javaClass.getResource("/conformance/warm_fade.fsim.json")!!.readText())

    private fun argb(a: Int, r: Int, g: Int, b: Int) = (a shl 24) or (r shl 16) or (g shl 8) or b

    @Test
    fun eachPixelIsTheRoundedLookupAndKeepsItsAlpha() {
        val pixels = IntArray(4096) { i -> argb(if (i % 7 == 0) 128 else 255, (i * 37) and 255, (i * 11) and 255, (i * 5 + 3) and 255) }
        val expected = IntArray(pixels.size)
        val out = FloatArray(3)
        for (i in pixels.indices) {
            val p = pixels[i]
            look.lut.lookup(((p shr 16) and 255) / 255f, ((p shr 8) and 255) / 255f, (p and 255) / 255f, out)
            val q = IntArray(3) { (out[it].coerceIn(0f, 1f) * 255f).roundToInt() }
            expected[i] = (p and -0x1000000) or (q[0] shl 16) or (q[1] shl 8) or q[2]
        }
        val actual = pixels.copyOf()
        look.lut.applyArgb(actual)
        assertContentEquals(expected, actual)
    }

    @Test
    fun appliesOnlyToTheGivenRange() {
        val pixels = IntArray(10) { argb(255, 10, 200, 90) }
        val before = pixels.copyOf()
        look.lut.applyArgb(pixels, from = 3, to = 6)
        for (i in pixels.indices) {
            if (i in 3 until 6) assert(pixels[i] != before[i]) else assertEquals(before[i], pixels[i])
        }
    }

    @Test
    fun identityLeavesPixelsUnchanged() {
        val pixels = IntArray(256 * 3) { i -> argb(255, i % 256, (i * 3) % 256, 255 - i % 256) }
        val before = pixels.copyOf()
        Lut3D.identity(33).applyArgb(pixels)
        assertContentEquals(before, pixels)
    }
}
