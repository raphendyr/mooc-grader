#!/bin/sh

if [ -z "$REC" -o -z "$SID" ]; then
    echo "An required environment variable REC or SID is missing." >&2
    exit 1
fi

download() {
    # download <url> <tmp file> <dest path>
    if ! curl \
        --fail --silent --show-error --http1.0 \
        --retry 10 --retry-delay 3 --connect-timeout 20 --max-time 60 --retry-connrefused \
        -H "Authorization: Bearer $SID" \
        "$1" -o "$2"
    then
        echo "Failed to download $1" >&2
        return 1
    fi

    mkdir -p "$3"
    tar -C "$3" -zxf "$2"
}

download "$REC/container/exercise.tar.gz" "/run/exercise.tar.gz" "/exercise/" &
download "$REC/container/submission.tar.gz" "/run/submission.tar.gz" "/submission/" &
if [ -d /personalized_exercise/ ]; then
    download "$REC/container/personalized.tar.gz" "/run/personalized.tar.gz" "/personalized_exercise/" &
fi

errors=0
for job in $(jobs -p); do
    wait $job || errors=$((errors + 1))
done

ls -Rl /submission/ /exercise/

rm -f /run/*.tar.gz
exit $errors
