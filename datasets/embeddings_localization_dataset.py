from typing import Tuple

import h5py
import torch
from Bio import SeqIO
from torch.utils.data import Dataset

from utils.general import LOCALIZATION


class EmbeddingsLocalizationDataset(Dataset):
    """
    Dataset of protein embeddings and the corresponding subcellular localization label.
    """

    def __init__(self, embeddings_path: str, remapped_sequences: str, unknown_solubility: bool = True,
                 descriptions_with_hash=True,
                 max_length: int = float('inf'),
                 embedding_mode: str = 'lm',
                 transform=lambda x: x) -> None:
        """Create dataset.
        Args:
            embeddings_path: embeddings_path: path to .hdf5 .h5 file with embeddings as generated by the bio_embeddings pipeline
                https://github.com/sacdallago/bio_embeddings. Can either be a file of reduced fixed length embeddings or of
                variable length embeddings.
            remapped_sequences: remapped_sequences_file.fasta as generated by bio_embeddings where the ids in the
                annotations are the keys for the .h5 file in the embeddings path
            unknown_solubility: Whether or not to include sequences with unknown solubility in the dataset
            transform: Pytorch torchvision transforms that should be applied to each sample
            max_length: bigger sequences wont be taken into the dataset
            embedding_mode: ['lm', 'onehot', 'profiles'] what type of protein encoding to return (lm stands for language model)
        """
        super().__init__()
        self.transform = transform
        self.embeddings_file = h5py.File(embeddings_path, 'r')
        self.localization_solubility_metadata_list = []
        self.class_weights = torch.zeros(10)
        self.one_hot_enc = []
        for record in SeqIO.parse(open(remapped_sequences), 'fasta'):
            if embedding_mode == 'onehot':
                self.one_hot_enc.append(None)
            if descriptions_with_hash:
                localization = record.description.split(' ')[2].split('-')[0]
                localization = LOCALIZATION.index(localization)  # get localization as integer
                solubility = record.description.split(' ')[2].split('-')[-1]
                id = str(record.id)
            else:
                localization = record.description.split(' ')[1].split('-')[0]
                localization = LOCALIZATION.index(localization)  # get localization as integer
                solubility = record.description.split(' ')[1].split('-')[-1]
                id = str(record.description)
            if len(record.seq) <= max_length:
                metadata = {'id': id,
                            'sequence': str(record.seq),
                            'length': len(record.seq),
                            'solubility_known': not (solubility == 'U')}

                # if unknown solubility is false only the sequences with known solubility are included
                if unknown_solubility or not (solubility == 'U'):
                    self.localization_solubility_metadata_list.append(
                        {'localization': localization, 'solubility': solubility, 'metadata': metadata})
            self.class_weights[localization] += 1
        self.class_weights /= self.class_weights.sum()

    def __getitem__(self, index: int) -> Tuple[torch.Tensor, torch.Tensor, torch.Tensor, dict]:
        """retrieve single sample from the dataset

        Args:
            index: index of sample to retrieve

        Returns:
            embedding: either a one dimensional Tensor [embedding_size] if the provided embeddings_path is of reduced
            embeddings or [length_of_sequence, embeddings_size] if the h5 file contains non reduced embeddings
            localization: localization in the format specified by the given transform.
            solubility: solubility as specified by a transform.
        """
        localization_solubility_metadata = self.localization_solubility_metadata_list[index]
        embedding = self.embeddings_file[localization_solubility_metadata['metadata']['id']][:]

        embedding, localization, solubility = self.transform(
            (embedding, localization_solubility_metadata['localization'],
             localization_solubility_metadata['solubility']))

        return embedding, localization, solubility, localization_solubility_metadata['metadata']

    def __len__(self) -> int:
        return len(self.localization_solubility_metadata_list)
