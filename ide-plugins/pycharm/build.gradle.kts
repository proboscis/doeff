plugins {
    id("org.jetbrains.intellij") version "1.17.2"
    kotlin("jvm") version "1.9.24"
}

intellij {
    version.set("2023.3")
    type.set("PC")
    plugins.set(listOf("PythonCore"))
}

repositories {
    mavenCentral()
}

dependencies {
    implementation("com.google.code.gson:gson:2.11.0")
}

kotlin {
    jvmToolchain(17)
}

tasks {
    patchPluginXml {
        sinceBuild.set("233")
        untilBuild.set("241.*")
    }

    buildSearchableOptions {
        enabled = false
    }

}
