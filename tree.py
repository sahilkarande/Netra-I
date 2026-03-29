import subprocess

# Set the output file path
output_file = "tree_output.txt"

# Run the 'tree' command
# On Windows, use 'tree /F'
# On Linux/macOS, use 'tree -a' (if 'tree' is installed)
try:
    result = subprocess.run(
        ["tree"],  # Replace with ["tree", "/F"] on Windows for full listing
        capture_output=True,
        text=True,
        check=True
    )

    # Write the output to a text file
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(result.stdout)

    print(f"Tree output saved to '{output_file}'")

except FileNotFoundError:
    print("Error: 'tree' command not found. Please install it first.")
except subprocess.CalledProcessError as e:
    print(f"Error running tree command: {e}")