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
