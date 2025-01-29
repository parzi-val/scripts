import subprocess
import os
import argparse

def convert_ppt_to_pdf(directory):
    directory = os.path.abspath(directory)
    
    if not os.path.isdir(directory):
        print(f"Error: Directory '{directory}' not found.")
        return
    
    ppt_files = [f for f in os.listdir(directory) if f.endswith((".ppt", ".pptx"))]

    if not ppt_files:
        print("No PowerPoint files found in the directory.")
        return

    for ppt_file in ppt_files:
        input_path = os.path.join(directory, ppt_file)
        
        command = [
            "soffice", "--headless", "--convert-to", "pdf",
            "--outdir", directory, input_path
        ]
        
        process = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        
        if process.returncode == 0:
            print(f"✅ Converted: {ppt_file} → PDF")
            os.remove(input_path)  
            print(f"🗑️ Deleted: {ppt_file}")
        else:
            print(f"❌ Failed to convert: {ppt_file}")
            print(process.stderr.decode())

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Convert all PPT/PPTX files in a directory to PDF.")
    parser.add_argument("directory", help="Path to the directory containing PPT/PPTX files.")
    
    args = parser.parse_args()
    convert_ppt_to_pdf(args.directory)
