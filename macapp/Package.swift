// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "SmoothieMenuBar",
    platforms: [
        .macOS(.v13),
    ],
    products: [
        .executable(name: "SmoothieMenuBar", targets: ["SmoothieMenuBar"]),
    ],
    targets: [
        .executableTarget(name: "SmoothieMenuBar"),
    ]
)
