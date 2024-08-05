#!/bin/bash
if [ -f update_info.json ]; then
      configs=$(jq -r 'keys[]' update_info.json)
      for config in $configs
      do
        new_update=$(jq -r ".[\"$config\"].found" update_info.json)
        if [ "$new_update" = "true" ]; then
          update_title=$(jq -r ".[\"$config\"].title" update_info.json)
          update_device=$(jq -r ".[\"$config\"].device" update_info.json)
          update_description=$(jq -r ".[\"$config\"].description" update_info.json)
          update_url=$(jq -r ".[\"$config\"].url" update_info.json)
          update_size=$(jq -r ".[\"$config\"].size" update_info.json)

          # Prepare release notes with proper Markdown formatting
          release_notes="
# $update_device

## Changelog:
$update_description

**Size:** $update_size

**Download URL:** $update_url
"

      # Check if a release with this title already exists
      if ! gh release view "$update_title" &> /dev/null; then
        gh release create "$update_title" \
              --title "$update_title" \
              --notes "$release_notes"
            echo "Created new release: $update_title for config $config"
          else
            echo "Release '$update_title' for config $config already exists. Skipping."
          fi
        else
          echo "No new updates found for config $config"
        fi
    done
else
    echo "update_info.json not found. This might be the first run."
fi
