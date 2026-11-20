from clearguard.app import main

if __name__ == '__main__':
    import sys
    main(smoke='--smoke-test' in sys.argv)
