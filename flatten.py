import os
import shutil
import sys

def flatten_directory(path):
    for root, dirs, files in os.walk(path, topdown=False):
        for name in files:
            file_path = os.path.join(root, name)
            shutil.move(file_path, path)
        for name in dirs:
            os.rmdir(os.path.join(root, name))

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python flatten.py <path>")
        sys.exit(1)

    directory_path = sys.argv[1]

    if not os.path.isdir(directory_path):
        print(f"The path {directory_path} is not a valid directory.")
        sys.exit(1)

    flatten_directory(directory_path)
    print(f"All files have been moved to {directory_path} and subdirectories have been deleted.")