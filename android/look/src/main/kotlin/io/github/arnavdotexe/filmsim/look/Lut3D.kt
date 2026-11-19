package io.github.arnavdotexe.filmsim.look

/**
 * A 3D LUT of [size] points per axis, sRGB-encoded in and out, stored in `.cube` order: red
 * changes fastest, then green, then blue, three floats per point.
 *
 * [lookup] is tetrahedral interpolation exactly as colorlab's `lut.apply_lut`: from the cell's
 * base corner it steps along the axis with the largest fraction first (ties: red, green, blue),
 * weighting each corner by the gaps between the sorted fractions.
 */
class Lut3D(val size: Int, private val data: FloatArray) {
    init {
        require(size >= 2) { "a LUT needs at least 2 points per axis, got $size" }
        require(data.size == size * size * size * 3) { "expected ${size * size * size * 3} values, got ${data.size}" }
    }

    /** A copy of the table in `.cube` order. */
    fun values(): FloatArray = data.copyOf()

    /** Look up one sRGB-encoded colour (clamped to 0..1) into [out] (at least 3 floats). */
    fun lookup(r: Float, g: Float, b: Float, out: FloatArray) {
        val n1 = size - 1
        val x = doubleArrayOf(clamp(r) * n1, clamp(g) * n1, clamp(b) * n1)
        val i0 = IntArray(3) { minOf(x[it].toInt(), size - 2) }
        val f = DoubleArray(3) { x[it] - i0[it] }
        // Stable descending order of the fractions: ties keep red, green, blue order.
        val order = intArrayOf(0, 1, 2)
        for (i in 1..2) {
            var j = i
            while (j > 0 && f[order[j]] > f[order[j - 1]]) {
                val t = order[j]
                order[j] = order[j - 1]
                order[j - 1] = t
                j--
            }
        }
        val w = doubleArrayOf(1 - f[order[0]], f[order[0]] - f[order[1]], f[order[1]] - f[order[2]], f[order[2]])
        val idx = i0.copyOf()
        var outR = w[0] * at(idx, 0)
        var outG = w[0] * at(idx, 1)
        var outB = w[0] * at(idx, 2)
        for (k in 0..2) {
            idx[order[k]] += 1
            outR += w[k + 1] * at(idx, 0)
            outG += w[k + 1] * at(idx, 1)
            outB += w[k + 1] * at(idx, 2)
        }
        out[0] = outR.toFloat()
        out[1] = outG.toFloat()
        out[2] = outB.toFloat()
    }

    /**
     * Apply the LUT in place to 8-bit ARGB pixels `[from, to)` (Android's `Bitmap` layout):
     * each colour is looked up and rounded back to 8 bits; alpha is kept.
     *
     * The same tetrahedral interpolation as [lookup], with the same arithmetic in the same order
     * (so every result is identical), but with no allocation per pixel: a photo has 12 million
     * pixels and [lookup] allocates six small arrays each, which made the garbage collector
     * dominate (2.4 s for a 12 MP photo).
     */
    fun applyArgb(pixels: IntArray, from: Int = 0, to: Int = pixels.size) {
        val n1 = (size - 1).toDouble()
        val last = size - 2
        val strideR = 3
        val strideG = 3 * size
        val strideB = 3 * size * size
        val d = data
        for (i in from until to) {
            val p = pixels[i]
            val xr = (((p shr 16) and 255) / 255f).toDouble() * n1
            val xg = (((p shr 8) and 255) / 255f).toDouble() * n1
            val xb = ((p and 255) / 255f).toDouble() * n1
            val ir = minOf(xr.toInt(), last)
            val ig = minOf(xg.toInt(), last)
            val ib = minOf(xb.toInt(), last)
            val fr = xr - ir
            val fg = xg - ig
            val fb = xb - ib
            // The stable descending order of the fractions (ties: red, green, blue): the axis with the
            // largest fraction is stepped first.
            val f1: Double; val f2: Double; val f3: Double
            val s1: Int; val s2: Int; val s3: Int
            if (fr >= fg) {
                if (fg >= fb) { f1 = fr; f2 = fg; f3 = fb; s1 = strideR; s2 = strideG; s3 = strideB }
                else if (fr >= fb) { f1 = fr; f2 = fb; f3 = fg; s1 = strideR; s2 = strideB; s3 = strideG }
                else { f1 = fb; f2 = fr; f3 = fg; s1 = strideB; s2 = strideR; s3 = strideG }
            } else {
                if (fr >= fb) { f1 = fg; f2 = fr; f3 = fb; s1 = strideG; s2 = strideR; s3 = strideB }
                else if (fg >= fb) { f1 = fg; f2 = fb; f3 = fr; s1 = strideG; s2 = strideB; s3 = strideR }
                else { f1 = fb; f2 = fg; f3 = fr; s1 = strideB; s2 = strideG; s3 = strideR }
            }
            val w0 = 1 - f1
            val w1 = f1 - f2
            val w2 = f2 - f3
            var idx = (ib * size + ig) * size * 3 + ir * 3
            var outR = w0 * d[idx]
            var outG = w0 * d[idx + 1]
            var outB = w0 * d[idx + 2]
            idx += s1
            outR += w1 * d[idx]; outG += w1 * d[idx + 1]; outB += w1 * d[idx + 2]
            idx += s2
            outR += w2 * d[idx]; outG += w2 * d[idx + 1]; outB += w2 * d[idx + 2]
            idx += s3
            outR += f3 * d[idx]; outG += f3 * d[idx + 1]; outB += f3 * d[idx + 2]
            pixels[i] = (p and -0x1000000) or (byte(outR.toFloat()) shl 16) or (byte(outG.toFloat()) shl 8) or byte(outB.toFloat())
        }
    }

    private fun byte(v: Float): Int = Math.round(v.coerceIn(0f, 1f) * 255f)

    private fun at(idx: IntArray, channel: Int): Double =
        data[((idx[2] * size + idx[1]) * size + idx[0]) * 3 + channel].toDouble()

    private fun clamp(v: Float): Double = v.toDouble().coerceIn(0.0, 1.0)

    companion object {
        /** The LUT that changes nothing. */
        fun identity(size: Int): Lut3D {
            val data = FloatArray(size * size * size * 3)
            var i = 0
            for (b in 0 until size) for (g in 0 until size) for (r in 0 until size) {
                data[i++] = r / (size - 1f)
                data[i++] = g / (size - 1f)
                data[i++] = b / (size - 1f)
            }
            return Lut3D(size, data)
        }
    }
}
