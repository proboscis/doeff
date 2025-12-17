plugins {
    id("org.jetbrains.intellij") version "1.17.2"
    kotlin("jvm") version "1.9.24"
}

val runIdeTests = providers.gradleProperty("runIdeTests")
    .map { it.equals("true", ignoreCase = true) }
    .orElse(false)

intellij {
    version.set("2024.3")
    type.set("PC")
    plugins.set(listOf("PythonCore"))
}

repositories {
    mavenCentral()
}

dependencies {
    implementation("com.google.code.gson:gson:2.11.0")
    testImplementation("junit:junit:4.13.2")
    testImplementation("org.opentest4j:opentest4j:1.3.0")
}

kotlin {
    jvmToolchain(17)
}

tasks {
    patchPluginXml {
        sinceBuild.set("243")
        untilBuild.set("")
    }

    buildSearchableOptions {
        enabled = false
    }

    test {
        enabled = runIdeTests.get()
        systemProperty("idea.platform.prefix", "PyCharmCore")
    }

}
