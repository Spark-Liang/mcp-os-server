import sys

if __name__ == "__main__":
    # Print arguments received
    print("Args:", sys.argv[1:])

    # Read and print stdin
    stdin_content = sys.stdin.read()
    if stdin_content:
        print("Stdin:", stdin_content)
