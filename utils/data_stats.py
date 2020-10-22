import os

from Bio import SeqIO
import pandas as pd
import matplotlib.pyplot as plt

fasta_path = '../data/fasta_files/train.fasta'
filename = os.path.basename(fasta_path)
if 'test' in filename:
    color = 'orange'
else:
    color = 'lightskyblue'

identifiers = []
labels = []
sequences = []
solubility = []
for record in SeqIO.parse(fasta_path, "fasta"):
    identifiers.append(record.id)
    labels.append(record.description.split(' ')[1].split('-')[0])
    sequences.append(str(record.seq))
    solubility.append(record.description.split(' ')[1].split('-')[-1])
df = pd.DataFrame(list(zip(identifiers, labels, solubility, sequences)),
                  columns=['identifier', 'label', 'solubility', 'seq'])
# df = df[df['solubility'] != 'U']
df['length'] = df['seq'].apply(lambda x: len(x))

fig, ax = plt.subplots()

df['label'].value_counts().plot(ax=ax, kind='bar')
plt.show()

print(df['label'].value_counts())

print('percentage of sequences larger than threshold AAs: {}'.format(100 * len(df[df['length'] > 1000]) / len(df)))
print(df[df['length'] > 6000])

cut_off = 2000
df[df['length'] < cut_off].hist(bins=50, ec='black', color=color)
plt.xlim((40, cut_off))  # there are only sequences longer than 40 in the datset
plt.title("Sequence lengths in {}".format(filename))
plt.xlabel("Sequence length")
plt.ylabel("Number sequences")
plt.show()

cut_off = 1500
axes = df[df['length'] < 1500].hist(by='label', ec='black', bins=30, color=color)
for rows in axes:
    for cell in rows:
        cell.set_xlim((40, cut_off))  # there are only sequences longer than 40 in the datset
        cell.tick_params(axis='x', which='both', labelbottom=False)
plt.show()
