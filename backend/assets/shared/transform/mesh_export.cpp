// Assimp preserves COLLADA scene transforms and units before SDF poses are applied.
#include <assimp/Importer.hpp>
#include <assimp/scene.h>
#include <assimp/postprocess.h>
#include <assimp/config.h>
#include <assimp/material.h>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <string>

static void quoted(std::ostream& out, const std::string& text) {
    out << '"';
    for (unsigned char c : text) {
        if (c == '"' || c == '\\') out << '\\' << c;
        else if (c < 32) out << "\\u00" << std::hex << std::setw(2) << std::setfill('0') << int(c) << std::dec;
        else out << c;
    }
    out << '"';
}
int main(int argc, char** argv) {
    if (argc != 3) return 2;
    Assimp::Importer importer;
    importer.SetPropertyBool(AI_CONFIG_IMPORT_COLLADA_IGNORE_UP_DIRECTION, true);
    const aiScene* scene = importer.ReadFile(argv[1], aiProcess_Triangulate | aiProcess_PreTransformVertices | aiProcess_JoinIdenticalVertices);
    if (!scene) { std::cerr << importer.GetErrorString() << '\n'; return 1; }
    std::ofstream out(argv[2]); out << std::setprecision(9) << '[';
    for (unsigned m = 0; m < scene->mNumMeshes; ++m) {
        const auto* mesh = scene->mMeshes[m];
        if (m) out << ',';
        out << "{\"vertices\":[";
        for (unsigned i = 0; i < mesh->mNumVertices; ++i) {
            const auto& v = mesh->mVertices[i];
            if (i) out << ','; out << '[' << v.x << ',' << v.y << ',' << v.z << ']';
        }
        out << "],\"faces\":["; bool first = true;
        for (unsigned i = 0; i < mesh->mNumFaces; ++i) {
            const auto& f = mesh->mFaces[i]; if (f.mNumIndices != 3) continue;
            if (!first) out << ','; first = false;
            out << '[' << f.mIndices[0] << ',' << f.mIndices[1] << ',' << f.mIndices[2] << ']';
        }
        out << "],\"uv\":[";
        if (mesh->HasTextureCoords(0)) for (unsigned i = 0; i < mesh->mNumVertices; ++i) {
            const auto& uv = mesh->mTextureCoords[0][i];
            if (i) out << ','; out << '[' << uv.x << ',' << uv.y << ']';
        }
        aiColor4D color(0.7f, 0.7f, 0.7f, 1.f); aiString texture;
        if (mesh->mMaterialIndex < scene->mNumMaterials) {
            auto* material = scene->mMaterials[mesh->mMaterialIndex];
            material->Get(AI_MATKEY_COLOR_DIFFUSE, color);
            material->GetTexture(aiTextureType_DIFFUSE, 0, &texture);
        }
        out << "],\"color\":[" << color.r << ',' << color.g << ',' << color.b << ',' << color.a << "],\"texture\":";
        quoted(out, texture.C_Str()); out << '}';
    }
    out << "]\n";
    return out ? 0 : 1;
}
