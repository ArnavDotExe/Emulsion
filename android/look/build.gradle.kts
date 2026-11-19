// The look model shared by the camera and the editor: the .fsim.json reader and a CPU
// reference renderer, plain Kotlin so its tests run on the JVM against colorlab's fixtures.
plugins {
    alias(libs.plugins.kotlin.jvm)
}

kotlin {
    jvmToolchain(21)
}

dependencies {
    implementation(libs.kotlinx.serialization.json)
    testImplementation(libs.kotlin.test.junit)
    testImplementation(libs.junit)
}

tasks.test {
    // The looks bundled in the app, checked by ShippedLooksTest.
    val shipped = rootProject.layout.projectDirectory.dir("app/src/main/assets/looks")
    inputs.dir(shipped).withPropertyName("shippedLooks")
    systemProperty("filmsim.shippedLooks", shipped.asFile.absolutePath)
}
