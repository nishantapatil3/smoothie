cask "smoothie" do
  version "0.0.3"
  url "https://github.com/nishantapatil3/smoothie/releases/download/v#{version}/Smoothie-#{version}.dmg"
  name "Smoothie"
  desc "Local-first audio transcription and summarization menu bar app"
  homepage "https://github.com/nishantapatil3/smoothie"

  # Replace :no_check with the release SHA-256 before publishing a release.
  sha256 :no_check

  app "Smoothie.app"

  zap trash: [
    "~/Library/Application Support/Smoothie",
    "~/Library/Preferences/com.smoothie.menu-bar.plist",
  ]
end
