#!/bin/bash

function stack {
    OutFileName="feats.csv"                       # Fix the output name
    i=0                                       # Reset a counter
    for filename in ./*.csv; do
        if [ "$filename"  != "$OutFileName" ]; then
            if [[ $i -eq 0 ]] ; then
                head -1  $filename >   $OutFileName # Copy header if it is the first file
            fi
            tail -n +2  $filename >>  $OutFileName # Append from the 2nd line each file
            i=$(( $i + 1 ))                        # Increase the counter
        fi
    done
}


DATADIR=$1
NUM_WORKERS=$2

rm -r $DATADIR
mkdir $DATADIR

cd $DATADIR/..
mkdir $DATADIR/{external,raw,interim,final,recipe} $DATADIR/interim/recipe $DATADIR/raw/recipe $DATADIR/final/recipe
mkdir $DATADIR/partd

# move it to the other directory and remove the junk
cp -r ./recipe-html/{HTML,Corrected} $DATADIR/recipe
# recode data
# convert both txt and html files
cd $DATADIR

for f in $(find . -name "*.txt" -o -name "*.html"); do
        encoding=$(file -i $f | cut -d"=" -f 2)  # get the mime encoding
        if [ "$encoding" != "us-ascii" ] && [ "$encoding" != "utf-8" ]; then
                res=$(chardetect $f)  # try to detect it otherwise
                encoding=$(echo $res | cut -d" " -f 2)
                echo $res - CONVERTING TO UTF-8
                recode ${encoding}..utf-8 $f
        fi
done


echo EXTRACTING LABELS/RAW
python -m learnhtml.cli.utils convert --blocks --num-workers $NUM_WORKERS  recipe raw/recipe

# extract features
echo EXTRACTING FEATURES
learnhtml dom --num-workers $NUM_WORKERS raw/recipe/raw.csv interim/recipe/feats-\*.csv

cd interim/recipe
stack
cd ../..

# merge data but do not split afterwards
echo MERGING CSVS
python -m learnhtml.cli.utils merge --cache ./partd --on "url,path" final/recipe/feats-\*.csv interim/recipe/feats.csv raw/recipe/labels.csv

cd final/recipe
stack
cd ../..

mv final/recipe/feats.csv ./recipe.csv

rm -r interim external raw partd recipe
