__author__ = 'JGJeffryes'

from rdkit.Chem import AllChem, Draw
from itertools import product
from collections import defaultdict
import time
from copy import deepcopy
import sys
import utils

from line_profiler import LineProfiler
def do_profile(func):
        def profiled_func(*args, **kwargs):
            try:
                profiler = LineProfiler()
                profiler.add_function(func)
                profiler.enable_by_count()
                return func(*args, **kwargs)
            finally:
                profiler.print_stats()
        return profiled_func

class Pickaxe:
    """
        This class generates new compounds from user-specified starting compounds using a set of SMARTS-based reaction
        rules. it may be initialized with a text file containing the reaction rules and cofactors or this may be
        done on an ad hock basis.
    """
    def __init__(self, rule_list=None, cofactor_list=None, explicit_h=True, strip_cof=True, kekulize=True, errors=True,
                 raceimze=False, mine=None):
        self.rxn_rules = {}
        self.cofactors = {}
        self.raw_compounds = {}
        self.compounds = {}
        self.reactions = []  # TODO: convert to dict with rxn hashing
        self.cof_set = {'[H+]'}
        self.mine = mine
        self.generation = 1
        self.explicit_h = explicit_h
        self.strip_cof = strip_cof
        self.kekulize = kekulize
        self.raceimize = raceimze
        if cofactor_list:
            with open(cofactor_list) as infile:
                for cofactor in infile:
                    self._load_cofactor(cofactor)
        self.cofactors['set'] = set()
        if rule_list:
            with open(rule_list) as infile:
                for rule in infile:
                    if rule[0] == "#":
                        continue
                    self.load_rxn_rule(rule)
        from rdkit import RDLogger
        lg = RDLogger.logger()
        if not errors:
            lg.setLevel(4)

    def _load_cofactor(self, cofactor_text):
        """
        Loads a cofactor into the cofactor dictionary from a tab-delimited string
        :param cofactor_text: tab-delimited string with the compound name and SMILES
        :type cofactor_text: basestring
        :return:
        :rtype:
        """
        split_text = cofactor_text.strip().split('\t')
        # add input validation
        mol = AllChem.MolFromSmiles(split_text[1])
        self.compounds[split_text[1]] = {'_id': split_text[0], 'SMILES': split_text[1], 'Generation': 0}
        if self.explicit_h:
            mol = AllChem.AddHs(mol)
        if self.kekulize:
            AllChem.Kekulize(mol)
            # also need to unset the aromatic flags in case the ring is hydrolysed (else will throw errors)
            for atom in mol.GetAtoms():
                atom.SetIsAromatic(False)
        self.cofactors[split_text[0]] = mol
        self.cof_set.add(split_text[1])

    def load_rxn_rule(self, rule_text):
        """
        Loads a reaction rule into the rxn_rule dictionary from a tab-delimited string
        :param rule_text: tab-delimited string with the rule name, cofactor names, and rule as SMARTS
        :type rule_text: basestring
        :return:
        :rtype:
        """
        split_text = rule_text.strip().split('\t')
        reactant_names = split_text[1].split(';')
        for name in reactant_names:
            if name == "Any":
                continue
            if name not in self.cofactors:  # try to proceed as if name is SMILES
                self._load_cofactor(name+"\t"+name)
        rxn = AllChem.ReactionFromSmarts(split_text[2])
        if rxn.GetNumReactantTemplates() != len(reactant_names):
            raise ValueError("Number of cofactors does not match supplied reaction rule")
        self.rxn_rules[split_text[0]] = (reactant_names, rxn)

    def transform_compound(self, compound_SMILES, rules=None):
        """
        Perform transformations to a compound returning the products and the predicted reactions
        :param compound_SMILES: The compound on which to operate represented as SMILES
        :type compound_SMILES: string
        :param rules: The names of the reaction rules to apply. If none, all rules in the pickaxe's dict will be used.
        :type rules: list
        :return: Transformed compounds as tuple of id abd SMILES string and reactions as a tuple of reactants & products
        :rtype: tuple of lists
        """
        if not rules:
            rules = self.rxn_rules.keys()
        mol = AllChem.MolFromSmiles(compound_SMILES)
        if self.kekulize:
            AllChem.Kekulize(mol)
            for atom in mol.GetAtoms():
                atom.SetIsAromatic(False)
        if self.explicit_h:
            mol = AllChem.AddHs(mol)
        self.cofactors['Any'] = mol
        rxns = []
        for rule_name in rules:
            rule = self.rxn_rules[rule_name]
            comps = tuple([self.cofactors[x] for x in rule[0]])
            try:
                ps = rule[1].RunReactants(comps)
            except RuntimeError:  # I need to do more to untangle the causes of this error
                print("Runtime ERROR!"+rule_name)
                continue
            reactants = self._make_compound_tups(comps, rule_name)
            for prods in ps:
                try:
                    products = self._make_compound_tups(prods, rule_name)
                    rid = str(len(self.reactions)+1).zfill(7)
                    reaction_data = {"_id":rid, "Reactants": reactants, "Products": products, "Operators": [rule_name]}
                    self.reactions.append(reaction_data)
                    rxns.append(reaction_data)
                except ValueError:
                    continue
        return [x for x in self.compounds.values()], rxns

    def _make_compound_tups(self, mols, rule_name):
        """takes a list of mol objects and returns (stoichometric, id) tuples"""
        comps = defaultdict(int)
        for m in mols:
            smiles = AllChem.MolToSmiles(m, True)
            if smiles not in self.raw_compounds:
                self._calculate_compound_information(smiles, m, rule_name)
            comps[self.raw_compounds[smiles]] += 1
        return [(y, x) for x, y in comps.items()]

    def _calculate_compound_information(self, raw, mol_obj, rule_name):
        if not self.mine:
            try:
                if self.explicit_h:
                    mol_obj = AllChem.RemoveHs(mol_obj) # this step slows down the process quite a bit
                if self.raceimize:
                    mols = self._racemization(mol_obj)
                else:
                    mols = [mol_obj]
                smiles = [AllChem.MolToSmiles(m, True) for m in mols]
            except ValueError:  # primarily seems to be caused by valence errors
                print("Product ERROR!: %s %s" % (rule_name, raw))
                raise ValueError
            for s in smiles:
                if s not in self.compounds:
                    cid = str(len(self.compounds)+1).zfill(7)
                    self.compounds[s] = {'_id': cid, "SMILES": s, 'Generation': self.generation}
            self.raw_compounds[raw] = tuple([self.compounds[s]['_id'] for s in smiles])

    def _racemization(self, compound, max_centers=3, carbon_only=True):
        """
        Enumerates all possible stereoisomers for unassigned chiral centers.
        :param compound: A compound
        :type compound: rdMol object
        :return: list of stereoisomers
        :rtype: list of rdMol objects
        """
        new_comps = []
        unassigned_centers = [c[0] for c in AllChem.FindMolChiralCenters(compound, includeUnassigned=True) if c[1] == "?"]
        if carbon_only:
            unassigned_centers = list(filter(lambda x: compound.GetAtomWithIdx(x).GetAtomicNum() == 6, unassigned_centers))
        if not unassigned_centers or len(unassigned_centers) > max_centers:
            return [compound]
        for seq in product([1, 0], repeat=len(unassigned_centers)):
            for atomId, cw in zip(unassigned_centers, seq):
                if cw:
                    compound.GetAtomWithIdx(atomId).SetChiralTag(AllChem.rdchem.ChiralType.CHI_TETRAHEDRAL_CW)
                else:
                    compound.GetAtomWithIdx(atomId).SetChiralTag(AllChem.rdchem.ChiralType.CHI_TETRAHEDRAL_CCW)
            new_comps.append(deepcopy(compound))
        return new_comps

    def write_compound_output_file(self, path, delimiter='\t'):
        """
        Writes all compound data to the specified path.
        :param path: path to output
        :type path: basestring
        :param delimiter: the character with which to separate data entries
        :type delimiter: basestring
        :return:
        :rtype:
        """
        #utils.prevent_overwrite(path)
        with open(path, 'w') as outfile:
            # TODO: use CSV dictwriter
            for c in sorted(pk.compounds.values(), key=lambda x: x['_id']):
                outfile.write('%s\t"%s"\n' % (c['_id'], c['SMILES']))

    def write_reaction_output_file(self, path, delimiter='\t'):
        """
        Writes all reaction data to the specified path.
        :param path: path to output
        :type path: basestring
        :param delimiter: the character with which to separate data entries
        :type delimiter: basestring
        :return:
        :rtype:
        """
        #utils.prevent_overwrite(path)
        with open(path, 'w') as outfile:
            # TODO: use CSV dictwriter
            for rxn in sorted(pk.reactions, key=lambda x: x['_id']):
                text_rxn = ' + '.join(['%s "%s"' % (x[0], x[1]) for x in rxn["Reactants"]]) + ' --> ' + \
                           ' + '.join(['%s "%s"' % (x[0], x[1]) for x in rxn["Products"]])
                outfile.write(delimiter.join([str(rxn['_id']), text_rxn, rxn['Operators'][0], str(rxn['Reactants']),
                                              str(rxn['Products'])])+'\n')


if __name__ == "__main__":
    t1 = time.time()
    pk = Pickaxe(cofactor_list="Cofactor_SMILES.tsv", rule_list="operators_smarts.tsv", raceimze=True)
    operators = defaultdict(int)
    seed_comps = []
    with open('test.tsv') as infile:
        for i, line in enumerate(infile):
            mol = AllChem.MolFromInchi(line.split('\t')[6])
            smi = AllChem.MolToSmiles(mol, True)
            pk.compounds[smi] = {'_id': line.split('\t')[0], "SMILES": smi, 'Genration': 0}
            seed_comps.append(smi)
    for i, smi in enumerate(seed_comps):
        print(i)
        prod, rxns = pk.transform_compound(smi)
        for r in rxns:
            operators[r["Operators"][0]] += 1
    for tup in sorted(operators.items(), key=lambda x: -x[1]):
        print(tup[0], tup[1])
    pk.write_compound_output_file('testcompounds')
    pk.write_reaction_output_file('testreactions')
    print(time.time()-t1)